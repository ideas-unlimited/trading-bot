"""Deploy bot: Binance market signals, demo orders on spot.

The loop polls production Binance klines for freshly *closed* bars, runs the
chosen strategy on them with the same no-lookahead semantics as the
backtester (a signal on bar *i* is acted on as bar *i+1* begins — here
approximated by a market order), and routes orders to
``demo-api.binance.com`` with demo keys.  There is no live-money order path.

Spot has no bracket orders, so every entry's stop lives in a small state file
and is checked on each closed bar.  The same file carries a Chandelier trail:
the stop ratchets to peak - trail_atr * ATR after each closed bar's exit
check, never downwards, so a bar cannot both raise the trail and fill against
it.  The venue is the source of truth for balances and equity; the state file
only remembers the last processed bar and the locally-managed exits.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .exchange.order_client import BinanceOrderClient, duration_for
from .config import BotConfig
from .data.feed import Bar
from .heartbeat import Heartbeat
from .notify import Notifier
from .risk import RiskManager
from .strategy.base import Action, Signal, Strategy


#: Base-asset residue below this USDT value is dust, not a position.  A
#: previous sleeve can leave a few cents behind (rounding/fees); counting it
#: as a position would block the next sleeve on the same symbol forever.
MIN_POSITION_NOTIONAL = 1.0


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


class DeployTrader:
    def __init__(
        self,
        client: BinanceOrderClient,
        strategy: Strategy,
        config: BotConfig,
        symbol: str,
        timeframe: str = "1Day",
        *,
        state_path: str | Path = "runs/deploy_state.json",
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
        startup_replay: bool = False,
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
        # A fresh start replays the strategy's whole allowed window so an open
        # position that began weeks ago is seen.  Other polls keep the much
        # smaller steady-state window; one full fetch is not a per-minute cost.
        _days = 730 if self.duration.total_seconds() >= 4 * 3600 else 365
        self.startup_lookback = max(
            self.lookback,
            math.ceil(_days * 86_400 / self.duration.total_seconds()),
        )
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
        for key in ("stops", "entries", "tps", "trails"):
            self.state.setdefault(key, {})
        risk_state = self.state.get("risk") or {}
        self.risk = RiskManager(
            config=self.config.risk,
            peak_equity=float(risk_state.get("peak_equity") or 0.0),
            halted=bool(risk_state.get("halted") or False),
            halt_reason=str(risk_state.get("halt_reason") or ""),
        )
        self._last_view: dict[str, Any] = {}
        self._trade_warned = False
        # Dashboard bookkeeping: a deploy loop registers itself once and then
        # writes equity + heartbeat every poll, so "is it running, and at
        # what rate" is answered by the database, not by hope.  A missing DB
        # must never stop trading or flood the log.
        self.record_dashboard = record
        self.bot_id: int | None = None
        self._db_warned = False
        # Trade alerts are a side effect of a booked fill, never a trading
        # path: a dead SMTP server must not leave a position unmanaged.
        self.notifier = notifier
        # Fresh-state bootstrap: replay the strategy over the fetched closed
        # bars to learn whether it is currently long (or has a pending entry),
        # then open that exposure at today's price.  Historical P&L is never
        # credited; this is a new position, not a copied fill.  Off by
        # default so tests and one-off runs keep the old anchor behaviour;
        # deploy/run_bot.sh turns it on for service bots.
        self.startup_replay = bool(startup_replay)
        self._last_equity = 0.0
        self._mark_price = 0.0
        self._dust_warned = False
        self._alert_warned = False
        # D8: signals, order rejects and errors reach the events table so the
        # UI can show why a bot did nothing at 3am.  Never spam: only real
        # signals, fills, halts and failures are recorded, never "no signal".
        self._events_warned = False
        self._halt_event_logged = False

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
        limit = self.lookback
        if self.startup_replay and self._last_ts() is None:
            limit = max(limit, self.startup_lookback)
        bars = self.client.fetch_bars(self.symbol, self.timeframe, limit=limit)
        # Some strategies only learn their warmup from the data itself: the
        # 200-day regime SMA is 1,200 4h bars, not the 25 the constructor
        # knows.  Prepare once, then refetch if the frame is still short.
        prepared = None
        if bars:
            from .backtest import bars_to_frame

            prepared = self.strategy.prepare(bars_to_frame(bars))
            needed = int(self.strategy.warmup) + 2
            self.lookback = max(self.lookback, needed)
            if len(bars) < needed:
                self._emit(
                    f"[deploy] {len(bars)} bars < warmup {needed}; "
                    f"refetching {self.lookback}"
                )
                bars = self.client.fetch_bars(
                    self.symbol, self.timeframe, limit=self.lookback,
                )
                prepared = (
                    self.strategy.prepare(bars_to_frame(bars)) if bars else None
                )
        if not bars or len(bars) < self.strategy.warmup + 2:
            self._emit(f"[deploy] only {len(bars)} bars fetched; waiting for more")
            self.heartbeat.ping()
            return 0

        account = self.client.get_account()
        position = self._position_qty()
        equity = self._book_equity(account)
        self._update_risk(equity)
        self._last_equity = equity

        # Register before reading the kill switch: without an id there is no
        # flag to read.  Halting must be visible within one poll.
        if self.record_dashboard and self.bot_id is None:
            self._ensure_bot(equity)
        if self._halted():
            if not self._halt_event_logged:
                # One row, not one per poll: the kill switch is already
                # visible on the dashboard while it holds.
                self._halt_event_logged = True
                self._event("halt", "kill switch engaged - closing position, no new entries",
                            payload={"symbol": self.symbol})
            if position > 0:
                self._exit(position, bars[-1].close, "halt", bars[-1].ts, equity=equity)
                position = self._position_qty()
                equity = self._book_equity(self.client.get_account()) or equity
                self._last_equity = equity
            self._emit(
                f"[deploy] HALTED by kill switch :: {self.symbol} "
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
            if self.startup_replay:
                # Reconstruct the strategy's current stance, then open it as
                # a NEW position at today's price.  The replay is state-only:
                # no historical fill is booked and no historical P&L is
                # credited.  Without this, a fresh start anchors past the
                # newest closed bar and can miss an entry that just fired.
                self._startup_replay(closed, now, equity)
                closed = []
            else:
                # Fresh state file: anchor to the newest closed bar instead of
                # replaying history.  Replaying would submit orders at today's
                # price for signals that fired months ago.
                self.state["last_ts"] = closed[-1].ts.isoformat()
                self._save_state()
                self._emit(
                    f"[deploy] fresh state: anchored to {closed[-1].ts:%Y-%m-%d %H:%M}, "
                    f"not trading {len(closed)} historical bars"
                )
                closed = []
        elif len(closed) > self.max_catchup:
            # Long gap (machine asleep, crash, days offline): acting on a
            # backlog is not the plan, so resume on the most recent bars only.
            skipped = len(closed) - self.max_catchup
            self._emit(
                f"[deploy] {skipped} missed bars skipped, resuming on the newest {self.max_catchup}"
            )
            closed = closed[-self.max_catchup:]
        if closed and prepared is None:
            from .backtest import bars_to_frame

            prepared = self.strategy.prepare(bars_to_frame(bars))
        for bar in closed:
            i = self._bar_index(prepared, bar)
            if i is None:
                continue
            # A position's exits and trail belong to the bar that just
            # closed.  Check the old stop first, then ratchet for the next
            # bar, and only then let the strategy signal at this close.
            self._check_exits_bar(bar)
            position = self._position_qty()
            if position > 0:
                self._ratchet_trail(bar, prepared, i)
            self._process_bar(
                bar, bars, equity, position, now, prepared=prepared, i=i,
            )
            position = self._position_qty()
            equity = self._book_equity(self.client.get_account()) or equity
            self._update_risk(equity)
        if closed:
            self._save_state()
        if not self._halted() and self._halt_event_logged:
            # Released: the next engagement is a new event, not a replay.
            self._halt_event_logged = False
            self._event("halt", "kill switch released - normal trading resumed",
                        payload={"symbol": self.symbol})
        else:
            # No new closed bar: still enforce an exit on the newest one,
            # which is what a restart onto an open position needs.
            self._check_exits(bars, now)
        position = self._position_qty()
        self._view(position, bars)
        self._status(bars, equity, position)
        self._record(equity, position, bars[-1].close if bars else None)
        # Alive, even when there was nothing to trade: the ping is the proof.
        self.heartbeat.ping()
        return len(closed)

    # -- dashboard bookkeeping ----------------------------------------------
    def _event(self, kind: str, message: str = "", *, level: str = "info",
               payload: dict[str, Any] | None = None) -> None:
        """Append one row to the events table (D8).

        Bookkeeping, not trading: a missing or broken database must never
        stop the loop, so failures warn once and are otherwise swallowed.
        Payloads must never carry secrets - only clientOrderId and venue
        error codes, like everywhere else.
        """
        if not (self.record_dashboard and self.bot_id):
            return
        from . import db as store

        try:
            conn = store.connect()
            try:
                store.log_event(conn, self.bot_id, kind, message,
                                level=level, payload=payload)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - the loop outlives the DB
            if not self._events_warned:
                self._events_warned = True
                self._emit(f"[deploy] event log off: {exc}")

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
                # A restart lands on the same row; the dashboard must show
                # this process's start, not the first one's.
                store.mark_bot_started(conn, self.bot_id)
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
                self._emit(f"[deploy] dashboard bookkeeping off: {exc}")

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
                self._emit(f"[deploy] trade history off: {exc}")

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
                "env": (os.environ.get("BOT_ENV") or "demo").strip().lower() or "demo",
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
                self._emit(f"[deploy] trade alerts failed: {type(exc).__name__}: {exc}")

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
        self._event("fill", f"SELL {qty:.4f} {self.symbol} @ {price:.4f} ({why})"
                    + (f" realized {realized:+.2f}" if realized is not None else ""),
                    payload={"side": "sell", "qty": qty, "price": price,
                             "closed_by": why, "realized": realized,
                             "order_id": str((order or {}).get("id") or ""),
                             "ts": ts.isoformat()})
        for key in ("stops", "tps", "entries", "trails"):
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

    def _update_risk(self, equity: float) -> None:
        """Track the high-water mark and persist the drawdown brake.

        The dry-run engine applies ``max_drawdown_stop`` to new entries; the
        trading loop must use the same knob or a manifest measured with a
        brake would be released with the brake silently disabled.  The peak
        is persisted because systemd restarts are routine, and a restart that
        reset the peak would reset the protection too.
        """
        was_halted = self.risk.halted
        self.risk.update_equity(float(equity))
        if self.risk.halted and not was_halted:
            self._emit(
                f"[deploy] drawdown brake: {self.risk.halt_reason}; "
                "no new entries (open position still managed)"
            )
        persisted = self.state.setdefault("risk", {})
        current = {
            "peak_equity": self.risk.peak_equity,
            "halted": self.risk.halted,
            "halt_reason": self.risk.halt_reason,
        }
        if persisted != current:
            persisted.update(current)
            self._save_state()

    def _startup_replay(
        self, closed: list[Bar], now: datetime, equity: float,
    ) -> None:
        """Bootstrap the strategy's current stance on a fresh state file.

        The replay uses only closed bars and the same Engine as the dry-run,
        so stops, targets, trails and time exits follow the measured rule.  If
        the model ends long (or has a BUY pending for the next open) this opens
        a NEW position at the newest closed bar's close.  Historical fills are
        deliberately not copied: the entry time, price and P&L are today's.
        """
        from .backtest import Engine, bars_to_frame

        last = closed[-1]
        self.state["last_ts"] = last.ts.isoformat()
        if self.risk.halted:
            self._emit(
                f"[deploy] startup replay: {len(closed)} bars; drawdown brake "
                f"({self.risk.halt_reason}), staying flat"
            )
            self._save_state()
            return
        try:
            engine = Engine(bars_to_frame(closed), self.strategy, self.config)
            while engine.step():
                pass
        except Exception as exc:  # noqa: BLE001 - a replay must not kill the loop
            self._emit(
                f"[deploy] startup replay unavailable: {exc}; "
                f"anchored to {last.ts:%Y-%m-%d %H:%M}"
            )
            self._save_state()
            return

        if engine.risk.halted:
            self._emit(
                f"[deploy] startup replay: {len(closed)} bars; replay drawdown "
                f"brake ({engine.risk.halt_reason}), staying flat"
            )
            self._save_state()
            return

        pending = engine._pending
        signal = None
        origin = ""
        if pending is not None and pending.limit_prices:
            self._emit(
                "[deploy] startup replay: model has a resting limit ladder; "
                "not converting it to a market entry"
            )
            self._save_state()
            return
        if pending is not None and pending.action is Action.CLOSE:
            # The model wants out at the next open: do not buy just to sell.
            pass
        elif pending is not None and pending.action is Action.BUY and not engine.broker.in_position:
            signal = pending
            origin = "pending entry"
        elif engine.broker.in_position:
            signal = Signal(
                Action.BUY,
                "startup replay",
                stop_loss=engine._stop,
                take_profit=engine._take,
                trail_atr=engine._trail_atr,
                trail_after_r=engine._trail_after_r,
                trail_after_price=engine._trail_after_price,
            )
            origin = (
                f"open position (entry {engine._entry_px:.4f})"
                if engine._entry_px is not None
                else "open position"
            )

        if signal is None:
            self._emit(
                f"[deploy] startup replay: {len(closed)} bars; model flat at "
                f"{last.ts:%Y-%m-%d %H:%M}, anchored without trading history"
            )
            self._save_state()
            return

        price = float(last.close)
        stop = signal.stop_loss
        take = signal.take_profit
        if stop is not None and (
            not math.isfinite(float(stop)) or float(stop) >= price
        ):
            self._emit(
                f"[deploy] startup replay: model long but stop {stop} is not "
                f"below {price:.4f}; staying flat"
            )
            self._save_state()
            return
        if take is not None and (
            not math.isfinite(float(take)) or float(take) <= price
        ):
            self._emit(
                f"[deploy] startup replay: model long but target {take} is not "
                f"above {price:.4f}; staying flat"
            )
            self._save_state()
            return

        qty = compute_entry_qty(
            equity, price, stop,
            risk_per_trade=self.config.risk.risk_per_trade,
            max_position_pct=self.config.risk.max_position_pct,
        )
        if qty * price < 1.0:
            self._emit(
                f"[deploy] startup replay: model long but "
                f"${qty * price:.2f} size is too small"
            )
            self._save_state()
            return

        order = self.client.submit_order(
            self.symbol, "buy", qty, price=price,
            stop=stop, take_profit=take,
        )
        filled = float(order.get("filled_qty") or qty)
        fill_px = float(order.get("filled_avg_price") or price)
        self.state.setdefault("stops", {}).pop(self.symbol, None)
        self.state.setdefault("tps", {}).pop(self.symbol, None)
        if stop is not None:
            self.state["stops"][self.symbol] = float(stop)
        if take is not None:
            self.state["tps"][self.symbol] = float(take)
        self.state.setdefault("entries", {})[self.symbol] = {
            "qty": filled, "price": fill_px, "ts": now.isoformat(),
        }
        self._set_trail(signal, fill_px, now)
        # The time exit starts now, not at a historical entry this book never held.
        if hasattr(self.strategy, "_entry_ts"):
            self.strategy._entry_ts = now
        self._book_fill(
            "buy", filled, fill_px, order_id=str(order.get("id") or ""),
        )
        self._alert_fill(
            "buy", filled, fill_px, why=signal.reason,
            stop=stop, take_profit=take, entry_ts=now, ts=now, equity=equity,
        )
        self._event(
            "fill",
            f"BUY {filled:.4f} {self.symbol} @ {fill_px:.4f} (startup replay)",
            payload={"side": "buy", "qty": filled, "price": fill_px,
                     "order_id": str(order.get("id") or ""), "stop": stop,
                     "take_profit": take, "reason": "startup replay",
                     "ts": now.isoformat()},
        )
        self._emit(
            f"[deploy] startup replay: {len(closed)} bars; {origin} -> BUY "
            f"{filled:.4f} {self.symbol} @ {fill_px:.4f}"
            + (f" stop={stop:.4f}" if stop is not None else "")
            + " (new entry now; historical fills/P&L not credited)"
        )
        self._save_state()

    def _last_ts(self) -> datetime | None:
        raw = self.state.get("last_ts")
        return datetime.fromisoformat(raw) if raw else None

    def _position_qty(self) -> float:
        pos = self.client.get_position(self.symbol)
        qty = float(pos["qty"]) if pos else 0.0
        # A balance the venue cannot sell is dust, not a position.  Leftovers
        # below the lot step (JUP 0.0949 against a step of 0.1) used to count as
        # long: the loop would arm a stop on two cents and then fail the floor
        # on every exit attempt - "quantity 0.0949 floors to 0.0" once a minute,
        # forever, on a bot with nothing left to trade.
        if qty > 0:
            floor = getattr(self.client, "filters", None)
            if callable(floor):
                try:
                    rules = floor(self.symbol)
                except Exception:  # noqa: BLE001 - never let a lookup stop the loop
                    rules = None
                if rules is not None:
                    try:
                        sellable = rules.floor_qty(qty)
                    except Exception:  # noqa: BLE001
                        sellable = qty
                    if sellable <= 0 or (rules.min_qty and sellable < rules.min_qty):
                        if not self.state.get("dust_logged"):
                            self.state["dust_logged"] = True
                            self._save_state()
                            self._emit(
                                f"[deploy] {qty:g} {self.symbol.split('/')[0]} left on the "
                                f"venue is below the lot step ({rules.step_size}); "
                                f"treating the book as flat"
                            )
                        return 0.0
        return qty

    def _bar_index(self, prepared: Any, bar: Bar) -> int | None:
        """Positional row for a closed bar in the prepared frame."""
        if prepared is None or "ts" not in prepared.columns:
            return None
        idx = (prepared["ts"] == bar.ts).to_numpy().nonzero()[0]
        return int(idx[0]) if len(idx) else None

    def _process_bar(
        self, bar: Bar, bars: list[Bar], equity: float, position: float, now: datetime,
        *, prepared: Any = None, i: int | None = None,
    ) -> None:
        if prepared is None or i is None:
            from .backtest import bars_to_frame

            prepared = self.strategy.prepare(bars_to_frame(bars))
            i = self._bar_index(prepared, bar)
            if i is None:
                return
        signal = self.strategy.signal(i, prepared, position)
        if signal is None:
            self._emit(f"[deploy] {bar.ts:%Y-%m-%d %H:%M} no signal")
            self.state["last_ts"] = bar.ts.isoformat()
            return

        if signal.action is Action.BUY and position <= 0:
            if self.risk.halted:
                self._emit(
                    f"[deploy] {bar.ts:%Y-%m-%d %H:%M} buy skipped: "
                    f"drawdown brake ({self.risk.halt_reason})"
                )
                self.state["last_ts"] = bar.ts.isoformat()
                return
            qty = compute_entry_qty(
                equity,
                bar.close,
                signal.stop_loss,
                risk_per_trade=self.config.risk.risk_per_trade,
                max_position_pct=self.config.risk.max_position_pct,
            )
            if qty * bar.close < 1.0:
                self._emit(f"[deploy] {bar.ts:%Y-%m-%d %H:%M} buy skipped: size ${qty * bar.close:.2f} too small")
                self._event("signal", f"buy skipped: size ${qty * bar.close:.2f} too small",
                            payload={"action": "buy", "ts": bar.ts.isoformat()})
            else:
                try:
                    order = self.client.submit_order(
                        self.symbol, "buy", qty,
                        price=bar.close,
                        stop=signal.stop_loss,
                        take_profit=signal.take_profit,
                    )
                except Exception as exc:
                    # Rejected at the venue: surface it in the UI, then let
                    # the usual poll-level catch keep the loop alive.  The
                    # bar stays unprocessed (last_ts not saved), so the next
                    # poll retries the same signal.
                    self._event("error", f"order rejected: {type(exc).__name__}: {exc}",
                                level="error",
                                payload={"side": "buy", "qty": qty,
                                         "price": bar.close})
                    raise
                # A new position owns its exits: never inherit a stale
                # target (SUI has none) from the previous trade.
                self.state["stops"].pop(self.symbol, None)
                self.state.setdefault("tps", {}).pop(self.symbol, None)
                if signal.stop_loss is not None:
                    self.state["stops"][self.symbol] = signal.stop_loss
                if signal.take_profit is not None:
                    self.state["tps"][self.symbol] = signal.take_profit
                # Book what the venue actually filled, not what was asked for.
                filled = float(order.get("filled_qty") or qty)
                fill_px = float(order.get("filled_avg_price") or bar.close)
                self.state.setdefault("entries", {})[self.symbol] = {
                    "qty": filled, "price": fill_px, "ts": bar.ts.isoformat(),
                }
                self._set_trail(signal, fill_px, bar.ts)
                self._book_fill(
                    "buy", filled, fill_px, order_id=str(order.get("id") or ""),
                )
                self._alert_fill(
                    "buy", filled, fill_px, why=signal.reason,
                    stop=signal.stop_loss, take_profit=signal.take_profit,
                    entry_ts=bar.ts, ts=bar.ts, equity=equity,
                )
                self._event("fill", f"BUY {filled:.4f} {self.symbol} @ {fill_px:.4f} :: {signal.reason}",
                            payload={"side": "buy", "qty": filled, "price": fill_px,
                                     "order_id": str(order.get("id") or ""),
                                     "stop": signal.stop_loss, "take_profit": signal.take_profit,
                                     "reason": signal.reason, "ts": bar.ts.isoformat()})
                self._emit(
                    f"[deploy] {bar.ts:%Y-%m-%d %H:%M} BUY {qty:.4f} {self.symbol} @ ~{bar.close:.2f}"
                    + (f" stop={signal.stop_loss:.2f}" if signal.stop_loss else " (no stop)")
                    + f" :: {signal.reason}"
                )
        elif signal.action is Action.CLOSE and position > 0:
            self._exit(position, bar.close, "signal", bar.ts, equity=equity)
            self._emit(f"[deploy] {bar.ts:%Y-%m-%d %H:%M} CLOSE {self.symbol} @ ~{bar.close:.2f} :: {signal.reason}")
        else:
            self._emit(f"[deploy] {bar.ts:%Y-%m-%d %H:%M} signal {signal.action.value} ignored (position {position})")
            self._event("signal", f"{signal.action.value} ignored (position {position})",
                        payload={"action": signal.action.value, "ts": bar.ts.isoformat()})
        self.state["last_ts"] = bar.ts.isoformat()

    def _set_trail(self, signal: Any, fill_px: float, ts: datetime) -> None:
        """Persist the trail that belongs to this entry, or clear it.

        The peak starts at the fill, not at a bar high that happened before
        the entry.  The deploy loop fills at the signal bar's close, so it must
        skip that same bar when it later ratchets.
        """
        trails = self.state.setdefault("trails", {})
        trail_atr = getattr(signal, "trail_atr", None)
        if trail_atr is None or not float(trail_atr) > 0:
            trails.pop(self.symbol, None)
            return
        risk0 = (
            float(fill_px) - float(signal.stop_loss)
            if signal.stop_loss is not None and float(signal.stop_loss) < float(fill_px)
            else None
        )
        trails[self.symbol] = {
            "atr": float(trail_atr),
            "after_r": float(getattr(signal, "trail_after_r", 0.0) or 0.0),
            "after_price": (
                float(signal.trail_after_price)
                if getattr(signal, "trail_after_price", None) is not None
                else None
            ),
            "entry": float(fill_px),
            "risk0": risk0,
            "peak": float(fill_px),
            "entry_ts": ts.isoformat(),
        }

    def _atr_at(self, prepared: Any, i: int) -> float | None:
        if prepared is None or i < 0 or i >= len(prepared):
            return None
        if "atr" in prepared.columns:
            value = float(prepared["atr"].iloc[i])
        else:
            from . import indicators as ind

            series = ind.atr(
                prepared["high"], prepared["low"], prepared["close"], 14,
            )
            value = float(series.iloc[i])
        return value if value > 0 else None

    def _ratchet_trail(self, bar: Bar, prepared: Any, i: int) -> None:
        """Raise this entry's stop to peak minus trail_atr ATRs, up only.

        This runs after the bar's exit check, so the new stop can only bind
        from the next bar onwards.  It also skips the entry bar because the deploy bot
        entries fill at that bar's close, after the high already happened.
        """
        trail = self.state.get("trails", {}).get(self.symbol)
        if not trail or self._position_qty() <= 0:
            return
        if trail.get("entry_ts") == bar.ts.isoformat():
            return
        trail_atr = float(trail.get("atr") or 0.0)
        if trail_atr <= 0:
            return

        high = float(bar.high)
        after_price = trail.get("after_price")
        if after_price is not None:
            if high < float(after_price):
                return
            # Armed once price touches the level; later dips cannot disarm it.
            trail["after_price"] = None

        after_r = float(trail.get("after_r") or 0.0)
        entry = float(trail.get("entry") or 0.0)
        risk0 = trail.get("risk0")
        if after_r > 0:
            if risk0 is None or float(risk0) <= 0 or entry <= 0:
                return
            if (float(bar.close) - entry) / float(risk0) < after_r:
                return

        peak = max(float(trail.get("peak") or entry), high)
        trail["peak"] = peak
        atr = self._atr_at(prepared, i)
        if atr is None:
            return
        candidate = peak - trail_atr * atr
        current = self.state.get("stops", {}).get(self.symbol)
        if current is None or candidate > float(current):
            self.state.setdefault("stops", {})[self.symbol] = candidate
            self._event("stop", f"trail raised to {candidate:.4f} (peak {peak:.4f} - {trail_atr:g} ATR)",
                        payload={"stop": candidate, "peak": peak, "ts": bar.ts.isoformat()})
            self._emit(
                f"[deploy] {bar.ts:%Y-%m-%d %H:%M} TRAIL stop={candidate:.4f} "
                f"(peak {peak:.4f} - {trail_atr:g} ATR {atr:.4f})"
            )

    def _check_exits_bar(self, bar: Bar) -> None:
        """Check one closed bar's stop then target, with gap fills."""
        entry = self.state.get("entries", {}).get(self.symbol) or {}
        if entry.get("ts") == bar.ts.isoformat():
            # Deploy fills at this bar's close; its low/high happened before
            # the position existed.  The first exit check is the next bar.
            return
        stop = self.state.get("stops", {}).get(self.symbol)
        target = self.state.get("tps", {}).get(self.symbol)
        qty = self._position_qty()
        if stop is not None and bar.low <= stop:
            # Gap-through: the open is the honest fill when it is already
            # below the stop.  A mere wick fills at the stop.
            price = min(float(bar.open), float(stop))
            self._exit(qty, price, "stop", bar.ts, equity=self._last_equity)
            self._emit(
                f"[deploy] {bar.ts:%Y-%m-%d %H:%M} STOP hit "
                f"({stop:.2f} >= low {bar.low:.2f}) -> closed {self.symbol}"
            )
            return
        if target is not None and bar.high >= target:
            price = max(float(bar.open), float(target))
            self._exit(qty, price, "target", bar.ts, equity=self._last_equity)
            self._emit(
                f"[deploy] {bar.ts:%Y-%m-%d %H:%M} TARGET hit "
                f"({target:.2f} <= high {bar.high:.2f}) -> closed {self.symbol}"
            )

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
        self._check_exits_bar(latest)

    def _status(self, bars: list[Bar], equity: float, position: float) -> None:
        if bars:
            latest = bars[-1]
            pos = f"{position:.4f} {self.symbol}" if position > 0 else "flat"
            self._emit(
                f"[deploy] {latest.ts:%Y-%m-%d %H:%M} close={latest.close:.2f} "
                f"position={pos} equity={equity:,.2f}"
            )

    # -- main loop ----------------------------------------------------------
    def run(self, *, once: bool = False) -> None:
        self._emit(
            f"[deploy] demo trading {self.symbol} @ {self.timeframe} on Binance DEMO account "
            f"(strategy {self.strategy.name}, poll {self.poll_seconds:.0f}s)"
        )
        self._emit(
            f"[deploy] trade alerts: {self.notifier.describe()}"
            if self.notifier is not None and self.notifier.enabled
            else "[deploy] trade alerts off (set TELEGRAM_*, NTFY_URL or SMTP_*)"
        )
        try:
            while True:
                try:
                    self.poll()
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    self._emit(f"[deploy] poll failed: {exc}")
                    self._event("error", f"poll failed: {type(exc).__name__}: {exc}",
                                level="error")
                if once:
                    break
                self.clock(self.poll_seconds)
        except KeyboardInterrupt:
            self._emit("[deploy] stopped")
