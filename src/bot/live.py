"""Live paper trading on Binance spot: production signals, demo orders.

The loop polls production Binance klines for freshly *closed* bars, runs the
chosen strategy on them with the same no-lookahead semantics as the
backtester (a signal on bar *i* is acted on as bar *i+1* begins — here
approximated by a market order), and routes orders to
``demo-api.binance.com`` with demo keys.  There is no production order path.

Spot has no bracket orders, so every entry's stop lives in a small state file
and is checked on each closed bar.  The venue is the source of truth for
balances and equity; the state file only remembers the last processed bar
and any locally-managed stop.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .exchange.live_client import BinanceLiveClient, duration_for
from .config import BotConfig
from .data.feed import Bar
from .heartbeat import Heartbeat
from .notify import Notifier
from .strategy.base import Action, Strategy


def compute_entry_qty(
    equity: float,
    price: float,
    stop: float | None,
    *,
    risk_per_trade: float,
    max_position_pct: float,
) -> float:
    """Position size from stop distance, capped by max position percent."""
    if stop is not None and 0 < stop < price:
        qty = equity * risk_per_trade / (price - stop)
    else:
        qty = equity * max_position_pct / price
    return min(qty, equity * max_position_pct / price)


class LiveTrader:
    def __init__(
        self,
        client: BinanceLiveClient,
        strategy: Strategy,
        config: BotConfig,
        symbol: str,
        timeframe: str = "1Day",
        *,
        state_path: str | Path = "runs/live_state.json",
        poll: float = 60.0,
        lookback: int = 400,
        max_catchup: int = 3,
        capital: float | None = None,
        heartbeat_url: str | None = None,
        heartbeat_every: float = 1800.0,
        stream: TextIO | None = None,
        clock: Any = time.sleep,
        record: bool = False,
        notifier: Notifier | None = None,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.config = config
        self.symbol = symbol
        self.timeframe = timeframe.strip()
        self.duration = duration_for(self.timeframe)
        self.state_path = Path(state_path)
        self.poll_seconds = max(1.0, poll)
        self.lookback = max(strategy.warmup + 2, lookback)
        self.max_catchup = max(1, max_catchup)
        # Per-bot wallet: the loop may never size from more than this, even
        # though several bots share one demo account.  None = the
        # whole account equity (the old behaviour).
        if capital is not None and capital <= 0:
            raise ValueError("capital must be positive when given")
        self.capital = capital
        # Dead-man's switch: an outside service notices when these pings stop.
        # A quiet market and a crashed process look identical from here.
        self.heartbeat = Heartbeat(heartbeat_url, every=heartbeat_every)
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock
        self.state: dict[str, Any] = self._load_state()
        # Older state files only carried last_ts + stops; give every loop the
        # same shape so the dashboard view never depends on how it started.
        for key in ("stops", "entries", "tps"):
            self.state.setdefault(key, {})
        self._last_view: dict[str, Any] = {}
        self._trade_warned = False
        # Dashboard bookkeeping: a live loop registers itself once and then
        # writes equity + heartbeat every poll, so "is it running, and at
        # what rate" is answered by the database, not by hope.  A missing DB
        # must never stop trading or flood the log.
        self.record_dashboard = record
        self.bot_id: int | None = None
        self._db_warned = False
        # Trade alerts are a side effect of a booked fill, never a trading
        # path: a dead SMTP server must not leave a position unmanaged.
        self.notifier = notifier
        self._last_equity = 0.0
        self._alert_warned = False

    # -- state --------------------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"last_ts": None, "stops": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    # -- output -------------------------------------------------------------
    def _emit(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    # -- one poll cycle -----------------------------------------------------
    def poll(self, now: datetime | None = None) -> int:
        """Run one cycle; returns the number of bars processed."""
        now = now or datetime.now(timezone.utc)
        bars = self.client.fetch_bars(self.symbol, self.timeframe, limit=self.lookback)
        if len(bars) < self.strategy.warmup + 2:
            self._emit(f"[live] only {len(bars)} bars fetched; waiting for more")
            self.heartbeat.ping()
            return 0

        account = self.client.get_account()
        position = self._position_qty()
        equity = self._book_equity(account)
        self._last_equity = equity

        # Register before reading the kill switch: without an id there is no
        # flag to read.  Halting must be visible within one poll.
        if self.record_dashboard and self.bot_id is None:
            self._ensure_bot(equity)
        if self._halted():
            if position > 0:
                self._exit(position, bars[-1].close, "halt", bars[-1].ts, equity=equity)
                position = self._position_qty()
                equity = self._book_equity(self.client.get_account()) or equity
                self._last_equity = equity
            self._emit(
                f"[live] HALTED by kill switch :: {self.symbol} "
                f"{'flat' if position <= 0 else 'position still open'}"
            )
            self._view(position, bars)
            self._record(equity, position, bars[-1].close if bars else None)
            self._status(bars, equity, position)
            self.heartbeat.ping()
            return 0

        last = self._last_ts()
        closed = [
            b for b in bars[:-1]
            if b.ts + self.duration <= now and (last is None or b.ts > last)
        ]
        if closed and last is None:
            # Fresh state file: anchor to the newest closed bar instead of
            # replaying history.  Replaying would submit orders at today's
            # price for signals that fired months ago.
            self.state["last_ts"] = closed[-1].ts.isoformat()
            self._save_state()
            self._emit(
                f"[live] fresh state: anchored to {closed[-1].ts:%Y-%m-%d %H:%M}, "
                f"not trading {len(closed)} historical bars"
            )
            closed = []
        elif len(closed) > self.max_catchup:
            # Long gap (machine asleep, crash, days offline): acting on a
            # backlog is not the plan, so resume on the most recent bars only.
            skipped = len(closed) - self.max_catchup
            self._emit(
                f"[live] {skipped} missed bars skipped, resuming on the newest {self.max_catchup}"
            )
            closed = closed[-self.max_catchup:]
        for bar in closed:
            self._process_bar(bar, bars, equity, position, now)
            position = self._position_qty()
            equity = self._book_equity(self.client.get_account()) or equity
        if closed:
            self._save_state()

        self._check_exits(bars, now)
        position = self._position_qty()
        self._view(position, bars)
        self._status(bars, equity, position)
        self._record(equity, position, bars[-1].close if bars else None)
        # Alive, even when there was nothing to trade: the ping is the proof.
        self.heartbeat.ping()
        return len(closed)

    # -- dashboard bookkeeping ----------------------------------------------
    def _ensure_bot(self, equity: float) -> None:
        """Register this run once so the dashboard can list it by name."""
        from . import db as store
        from .envcfg import env_name

        name = f"{self.symbol.replace('/', '')}-{self.strategy.name}"
        conn = store.connect()
        try:
            row = conn.execute(
                "SELECT id FROM bots WHERE name = %s AND status = 'running' "
                "ORDER BY id DESC LIMIT 1", (name,)
            ).fetchone()
            if row:
                self.bot_id = int(row["id"])
            else:
                bot = store.register_bot(
                    conn, name,
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    strategy_name=self.strategy.name,
                    params={
                        k: v for k, v in (getattr(self.strategy, "params", None) or {}).items()
                        if isinstance(v, (str, int, float, bool))
                    },
                    capital=float(self.capital or equity),
                    env=env_name(),
                )
                self.bot_id = int(bot["id"])
            conn.commit()
        finally:
            conn.close()

    def _record(self, equity: float, position: float, price: float | None) -> None:
        from . import db as store

        if not self.record_dashboard:
            return
        try:
            if self.bot_id is None:
                self._ensure_bot(equity)
            conn = store.connect()
            try:
                store.record_equity(
                    conn, self.bot_id,
                    equity=equity, position_qty=position, mark_price=price,
                )
                view = dict(self._last_view)
                view["equity"] = equity
                store.set_bot_state(conn, self.bot_id, view)
                store.bot_heartbeat(conn, self.bot_id)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - never stop trading for bookkeeping
            if not self._db_warned:
                self._db_warned = True
                self._emit(f"[live] dashboard bookkeeping off: {exc}")

    def _halted(self) -> bool:
        """Read the kill switch; a database hiccup must never stop the loop."""
        from . import db as store

        if not self.record_dashboard or self.bot_id is None:
            return False
        try:
            conn = store.connect()
            try:
                return store.bot_halted(conn, self.bot_id)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return False

    def _view(self, position: float, bars: list[Bar]) -> None:
        """Snapshot for the dashboard: what the bot holds and what protects it."""
        entry = self.state.get("entries", {}).get(self.symbol) or {}
        self._last_view = {
            "position_qty": float(position),
            "entry_price": entry.get("price"),
            "opened_at": entry.get("ts"),
            "stop": self.state.get("stops", {}).get(self.symbol),
            "take_profit": self.state.get("tps", {}).get(self.symbol),
            "mark_price": bars[-1].close if bars else None,
            "last_bar_ts": bars[-1].ts.isoformat() if bars else None,
        }

    def _book_fill(
        self,
        side: str,
        qty: float,
        price: float,
        *,
        realized: float | None = None,
        closed_by: str = "",
        order_id: str = "",
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
    ) -> None:
        """Write the fill to bot_trades so the dashboard has real history."""
        from . import db as store

        if not (self.record_dashboard and self.bot_id):
            return
        fee = abs(float(qty) * float(price)) * self.config.costs.fee_bps / 10_000.0
        try:
            conn = store.connect()
            try:
                store.record_trade(
                    conn, self.bot_id,
                    symbol=self.symbol, side=side, qty=qty, price=price, fee=fee,
                    realized_pnl=realized, opened_at=opened_at, closed_at=closed_at,
                    closed_by=closed_by, order_id=order_id,
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            if not self._trade_warned:
                self._trade_warned = True
                self._emit(f"[live] trade history off: {exc}")

    def _alert_fill(
        self,
        side: str,
        qty: float,
        price: float,
        *,
        why: str = "",
        stop: float | None = None,
        take_profit: float | None = None,
        realized: float | None = None,
        entry_price: float | None = None,
        entry_ts: datetime | None = None,
        ts: datetime | None = None,
        equity: float | None = None,
    ) -> None:
        """One best-effort alert for a fill the venue already accepted."""
        if self.notifier is None or not self.notifier.enabled:
            return
        try:
            held_hours = None
            if entry_ts is not None and ts is not None:
                held_hours = (ts - entry_ts).total_seconds() / 3600.0
            return_pct = None
            if realized is not None and entry_price and qty:
                return_pct = realized / (entry_price * qty)
            event = {
                "side": side,
                "strategy": self.strategy.name,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "env": (os.environ.get("BOT_ENV") or "paper").strip().lower() or "paper",
                "ts": ts,
                "entry": entry_price if entry_price is not None else (price if side == "buy" else None),
                "price": price,
                "qty": qty,
                "notional": float(qty) * float(price),
                "stop": stop,
                "take_profit": take_profit,
                "reason": why,
                "pnl": realized,
                "return_pct": return_pct,
                "held_hours": held_hours,
                "wallet": equity if equity is not None else self._last_equity,
            }
            self.notifier.send_trade(event)
        except Exception as exc:  # noqa: BLE001 - never stop managing a position
            if not self._alert_warned:
                self._alert_warned = True
                self._emit(f"[live] trade alerts failed: {type(exc).__name__}: {exc}")

    def _exit(self, qty: float, price: float, why: str, ts: datetime,
              *, equity: float | None = None) -> None:
        """Close the position and record the sell with its realized P&L."""
        entry = self.state.get("entries", {}).get(self.symbol) or {}
        entry_price = float(entry.get("price") or 0.0)
        stop = self.state.get("stops", {}).get(self.symbol)
        take_profit = self.state.get("tps", {}).get(self.symbol)
        entry_ts = None
        if entry.get("ts"):
            try:
                entry_ts = datetime.fromisoformat(str(entry["ts"]))
            except ValueError:
                entry_ts = None
        order = self.client.close_position(self.symbol)
        realized = (float(price) - entry_price) * float(qty) if entry_price and qty else None
        self._book_fill(
            "sell", qty, price, realized=realized, closed_by=why,
            order_id=str((order or {}).get("id") or ""), closed_at=ts,
        )
        for key in ("stops", "tps", "entries"):
            self.state.get(key, {}).pop(self.symbol, None)
        self._save_state()
        # State is saved before the (possibly slow) alert, so a crash during
        # delivery cannot make the loop close the same position twice.
        self._alert_fill(
            "sell", qty, price, why=why, stop=stop,
            take_profit=take_profit, realized=realized,
            entry_price=entry_price, entry_ts=entry_ts, ts=ts, equity=equity,
        )

    def _book_equity(self, account: dict[str, Any]) -> float:
        """Equity this bot may size from: honours the capital cap when set."""
        equity = float(account.get("equity") or 0.0)
        return min(equity, self.capital) if self.capital is not None else equity

    def _last_ts(self) -> datetime | None:
        raw = self.state.get("last_ts")
        return datetime.fromisoformat(raw) if raw else None

    def _position_qty(self) -> float:
        pos = self.client.get_position(self.symbol)
        return float(pos["qty"]) if pos else 0.0

    def _process_bar(
        self, bar: Bar, bars: list[Bar], equity: float, position: float, now: datetime
    ) -> None:
        from .backtest import bars_to_frame

        df = bars_to_frame(bars)
        prepared = self.strategy.prepare(df)
        idx = df.index[df["ts"] == bar.ts]
        if len(idx) == 0:
            return
        i = int(idx[0])
        signal = self.strategy.signal(i, prepared, position)
        if signal is None:
            self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} no signal")
            self.state["last_ts"] = bar.ts.isoformat()
            return

        if signal.action is Action.BUY and position <= 0:
            qty = compute_entry_qty(
                equity,
                bar.close,
                signal.stop_loss,
                risk_per_trade=self.config.risk.risk_per_trade,
                max_position_pct=self.config.risk.max_position_pct,
            )
            if qty * bar.close < 1.0:
                self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} buy skipped: size ${qty * bar.close:.2f} too small")
            else:
                order = self.client.submit_order(
                    self.symbol, "buy", qty,
                    price=bar.close,
                    stop=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                if signal.stop_loss is not None:
                    self.state["stops"][self.symbol] = signal.stop_loss
                if signal.take_profit is not None:
                    self.state.setdefault("tps", {})[self.symbol] = signal.take_profit
                # Book what the venue actually filled, not what was asked for.
                filled = float(order.get("filled_qty") or qty)
                fill_px = float(order.get("filled_avg_price") or bar.close)
                self.state.setdefault("entries", {})[self.symbol] = {
                    "qty": filled, "price": fill_px, "ts": bar.ts.isoformat(),
                }
                self._book_fill(
                    "buy", filled, fill_px, order_id=str(order.get("id") or ""),
                )
                self._alert_fill(
                    "buy", filled, fill_px, why=signal.reason,
                    stop=signal.stop_loss, take_profit=signal.take_profit,
                    entry_ts=bar.ts, ts=bar.ts, equity=equity,
                )
                self._emit(
                    f"[live] {bar.ts:%Y-%m-%d %H:%M} BUY {qty:.4f} {self.symbol} @ ~{bar.close:.2f}"
                    + (f" stop={signal.stop_loss:.2f}" if signal.stop_loss else " (no stop)")
                    + f" :: {signal.reason}"
                )
        elif signal.action is Action.CLOSE and position > 0:
            self._exit(position, bar.close, "signal", bar.ts, equity=equity)
            self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} CLOSE {self.symbol} @ ~{bar.close:.2f} :: {signal.reason}")
        else:
            self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} signal {signal.action.value} ignored (position {position})")
        self.state["last_ts"] = bar.ts.isoformat()

    def _check_exits(self, bars: list[Bar], now: datetime) -> None:
        """Spot stops and targets are local: Binance spot has no brackets.

        The stop is checked before the target, exactly like the engine: a bar
        that touches both is booked as a loss, never as a win.  A target only
        exists while the entry that set it is still open.
        """
        if not bars:
            return
        latest = bars[-1]
        if latest.ts + self.duration > now:
            return
        stop = self.state.get("stops", {}).get(self.symbol)
        target = self.state.get("tps", {}).get(self.symbol)
        qty = self._position_qty()
        if stop is not None and latest.low <= stop:
            self._exit(qty, stop, "stop", latest.ts, equity=self._last_equity)
            self._emit(f"[live] {latest.ts:%Y-%m-%d %H:%M} STOP hit ({stop:.2f} <= low {latest.low:.2f}) -> closed {self.symbol}")
            return
        if target is not None and latest.high >= target:
            # Fill at the target level, the same convention the backtest uses.
            self._exit(qty, target, "target", latest.ts, equity=self._last_equity)
            self._emit(f"[live] {latest.ts:%Y-%m-%d %H:%M} TARGET hit ({target:.2f} <= high {latest.high:.2f}) -> closed {self.symbol}")

    def _status(self, bars: list[Bar], equity: float, position: float) -> None:
        if bars:
            latest = bars[-1]
            pos = f"{position:.4f} {self.symbol}" if position > 0 else "flat"
            self._emit(
                f"[live] {latest.ts:%Y-%m-%d %H:%M} close={latest.close:.2f} "
                f"position={pos} equity={equity:,.2f}"
            )

    # -- main loop ----------------------------------------------------------
    def run(self, *, once: bool = False) -> None:
        self._emit(
            f"[live] paper trading {self.symbol} @ {self.timeframe} on Binance DEMO account "
            f"(strategy {self.strategy.name}, poll {self.poll_seconds:.0f}s)"
        )
        self._emit(
            f"[live] trade alerts: {self.notifier.describe()}"
            if self.notifier is not None and self.notifier.enabled
            else "[live] trade alerts off (set TELEGRAM_*, NTFY_URL or SMTP_*)"
        )
        try:
            while True:
                try:
                    self.poll()
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    self._emit(f"[live] poll failed: {exc}")
                if once:
                    break
                self.clock(self.poll_seconds)
        except KeyboardInterrupt:
            self._emit("[live] stopped")
