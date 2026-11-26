"""Live paper trading against Alpaca's paper account.

The loop polls Alpaca for freshly *closed* bars, runs the chosen strategy on
them with the same no-lookahead semantics as the backtester (a signal on bar
*i* is acted on as bar *i+1* begins — here approximated by a market order),
and routes orders to ``paper-api.alpaca.markets``:

* stock entries go out as **bracket orders** with the strategy's ATR stop;
* crypto entries are simple market orders (Alpaca crypto has no brackets) and
  the stop is managed locally in a small state file.

Alpaca is the source of truth for positions and equity; the state file only
remembers the last processed bar and any locally-managed crypto stop.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .alpaca_client import TIMEFRAME_DURATION, AlpacaClient, resolve_timeframe, symbol_is_crypto
from .config import BotConfig
from .data.feed import Bar
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
        client: AlpacaClient,
        strategy: Strategy,
        config: BotConfig,
        symbol: str,
        timeframe: str = "1Day",
        *,
        state_path: str | Path = "runs/live_state.json",
        poll: float = 60.0,
        lookback: int = 400,
        stream: TextIO | None = None,
        clock: Any = time.sleep,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.config = config
        self.symbol = symbol
        self.timeframe = resolve_timeframe(timeframe)
        self.duration = TIMEFRAME_DURATION[self.timeframe]
        self.state_path = Path(state_path)
        self.poll_seconds = max(1.0, poll)
        self.lookback = max(strategy.warmup + 2, lookback)
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock
        self.state: dict[str, Any] = self._load_state()

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
            return 0

        account = self.client.get_account()
        position = self._position_qty()
        equity = float(account.get("equity") or 0.0)

        last = self._last_ts()
        closed = [
            b for b in bars[:-1]
            if b.ts + self.duration <= now and (last is None or b.ts > last)
        ]
        for bar in closed:
            self._process_bar(bar, bars, equity, position, now)
            position = self._position_qty()
            equity = float(self.client.get_account().get("equity") or equity)
        if closed:
            self._save_state()

        self._check_crypto_stop(bars, now)
        self._status(bars, equity, position)
        return len(closed)

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
                self.client.submit_order(
                    self.symbol, "buy", qty,
                    stop=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                if signal.stop_loss is not None and symbol_is_crypto(self.symbol):
                    self.state["stops"][self.symbol] = signal.stop_loss
                self._emit(
                    f"[live] {bar.ts:%Y-%m-%d %H:%M} BUY {qty:.4f} {self.symbol} @ ~{bar.close:.2f}"
                    + (f" stop={signal.stop_loss:.2f}" if signal.stop_loss else " (no stop)")
                    + f" :: {signal.reason}"
                )
        elif signal.action is Action.CLOSE and position > 0:
            self.client.close_position(self.symbol)
            self.state["stops"].pop(self.symbol, None)
            self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} CLOSE {self.symbol} @ ~{bar.close:.2f} :: {signal.reason}")
        else:
            self._emit(f"[live] {bar.ts:%Y-%m-%d %H:%M} signal {signal.action.value} ignored (position {position})")
        self.state["last_ts"] = bar.ts.isoformat()

    def _check_crypto_stop(self, bars: list[Bar], now: datetime) -> None:
        """Crypto stops are managed locally (Alpaca crypto has no brackets)."""
        stop = self.state.get("stops", {}).get(self.symbol)
        if stop is None or not bars:
            return
        latest = bars[-1]
        if latest.low <= stop and latest.ts + self.duration <= now:
            self.client.close_position(self.symbol)
            self.state["stops"].pop(self.symbol, None)
            self._emit(f"[live] {latest.ts:%Y-%m-%d %H:%M} STOP hit ({stop:.2f} <= low {latest.low:.2f}) -> closed {self.symbol}")
            self._save_state()

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
            f"[live] paper trading {self.symbol} @ {self.timeframe} on Alpaca PAPER account "
            f"(strategy {self.strategy.name}, poll {self.poll_seconds:.0f}s)"
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
