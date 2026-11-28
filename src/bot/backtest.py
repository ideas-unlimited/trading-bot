"""Event-driven bar-by-bar engine.

Execution model (no lookahead):

* A strategy sees bar *i*'s completed OHLCV and may emit a signal.
* That signal is filled at the **open of bar i+1**, with slippage and fees.
* Stops / take-profits are checked intrabar on high/low.  If both could have
  triggered in the same bar we assume the stop filled first (conservative).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .broker import Broker, Fill, Trade
from .config import BotConfig
from .data.feed import Bar
from .risk import RiskManager
from .strategy.base import Action, Signal, Strategy

OHLCV = ("open", "high", "low", "close")


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    if not bars:
        raise ValueError("no bars to backtest")
    return pd.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


@dataclass
class BacktestResult:
    strategy: str
    initial_equity: float
    equity_curve: pd.Series  # indexed by timestamp
    close: pd.Series  # underlying price, for benchmark comparison
    trades: list[Trade]
    fills: list[Fill]
    exposure: pd.Series  # fraction of equity in the position per bar
    halted_reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if len(self.equity_curve) else self.initial_equity


class Engine:
    """Stateful simulator; one ``step()`` per bar.  Drives backtests and replay."""

    def __init__(self, df: pd.DataFrame, strategy: Strategy, config: BotConfig | None = None):
        missing = [c for c in OHLCV if c not in df.columns]
        if missing:
            raise ValueError(f"data missing columns: {missing}")
        if len(df) < 2:
            raise ValueError("need at least 2 bars")
        self.config = config or BotConfig()
        self.strategy = strategy
        self.df = strategy.prepare(df)
        self.broker = Broker(cash=self.config.initial_cash, costs=self.config.costs)
        self.risk = RiskManager(config=self.config.risk)
        self.risk.update_equity(self.config.initial_cash)

        if "atr" in self.df.columns:
            self._atr = self.df["atr"]
        else:
            from . import indicators as ind

            self._atr = ind.atr(self.df["high"], self.df["low"], self.df["close"], 14)

        self.i = 0
        self._pending: Signal | None = None
        self._stop: float | None = None
        self._take: float | None = None
        self._trail_atr: float | None = None
        self._trail_after_r: float = 0.0
        self._entry_px: float | None = None
        self._risk0: float | None = None
        self._peak: float | None = None
        self._equity: list[tuple[datetime, float]] = []
        self._exposure: list[tuple[datetime, float]] = []
        self._finished = False

    # -- public API ------------------------------------------------------
    def step(self) -> bool:
        """Process the next bar.  Returns False when the data is exhausted."""
        if self._finished or self.i >= len(self.df):
            return False

        row = self.df.iloc[self.i]
        ts = row["ts"]
        open_px = float(row["open"])

        if self._pending is not None:
            self._execute(self._pending, ts, open_px)
            self._pending = None

        self._check_exits(row, ts)
        self._ratchet_trail(row)

        equity = self.broker.equity(float(row["close"]))
        self.risk.update_equity(equity)
        self._equity.append((ts, equity))
        self._exposure.append((ts, self.broker.market_value(float(row["close"])) / equity if equity else 0.0))

        self._maybe_signal()
        self.i += 1
        return True

    def run(self) -> None:
        while self.step():
            pass
        self.finish()

    def finish(self) -> None:
        """Force-close any open position at the last close and stop the engine."""
        if self._finished:
            return
        self._finished = True
        if self.broker.in_position:
            last = self.df.iloc[-1]
            self.broker.sell(last["ts"], float(last["close"]), reason="end of data")

    def result(self) -> BacktestResult:
        if not self._finished:
            self.finish()
        equity = pd.Series(
            [v for _, v in self._equity], index=pd.Index([t for t, _ in self._equity], name="ts")
        )
        exposure = pd.Series(
            [v for _, v in self._exposure], index=equity.index
        )
        close = pd.Series(
            self.df["close"].to_numpy(dtype=float), index=equity.index, name="close"
        )
        return BacktestResult(
            strategy=self.strategy.name,
            initial_equity=self.config.initial_cash,
            equity_curve=equity,
            close=close,
            trades=list(self.broker.trades),
            fills=list(self.broker.fills),
            exposure=exposure,
            halted_reason=self.risk.halt_reason,
        )

    # -- internals -------------------------------------------------------
    def _execute(self, signal: Signal, ts: datetime, open_px: float) -> None:
        if signal.action is Action.BUY:
            if self.broker.in_position:
                return
            equity = self.broker.equity(open_px)
            qty = self.risk.size(equity, open_px, signal.stop_loss)
            if qty <= 0:
                return
            fill = self.broker.buy(ts, open_px, qty, reason=signal.reason)
            if fill is not None:
                self._stop = signal.stop_loss
                self._take = signal.take_profit
                self._trail_atr = signal.trail_atr
                self._trail_after_r = signal.trail_after_r
                self._entry_px = fill.price
                self._risk0 = fill.price - signal.stop_loss
                self._peak = fill.price
            return

        # SELL / CLOSE
        if self.broker.in_position:
            self.broker.sell(ts, open_px, reason=signal.reason)
            self._stop = None
            self._take = None
            self._trail_atr = None
            self._trail_after_r = 0.0
            self._entry_px = None
            self._risk0 = None
            self._peak = None

    def _check_exits(self, row: pd.Series, ts: datetime) -> None:
        if not self.broker.in_position:
            return
        low, high, open_px = float(row["low"]), float(row["high"]), float(row["open"])

        if self._stop is not None and low <= self._stop:
            # Gap-through: fill at the open if it is already below the stop.
            price = min(open_px, self._stop)
            self.broker.sell(ts, price, reason=f"stop loss ({self._stop:.4f})")
            self._stop = self._take = None
            self._trail_atr = None
            self._trail_after_r = 0.0
            self._entry_px = None
            self._risk0 = None
            self._peak = None
            return
        if self._take is not None and high >= self._take:
            price = max(open_px, self._take)
            self.broker.sell(ts, price, reason=f"take profit ({self._take:.4f})")
            self._stop = self._take = None
            self._trail_atr = None
            self._trail_after_r = 0.0
            self._entry_px = None
            self._risk0 = None
            self._peak = None

    def _ratchet_trail(self, row: pd.Series) -> None:
        """Raise the stop to peak minus trail_atr ATRs; it never moves down.

        The new stop is computed after this bar's exit check, so it can only
        bind from the next bar onwards: no same-bar trail-and-fill.
        """
        if not self._trail_atr or not self.broker.in_position:
            return
        if self._trail_after_r > 0:
            if not self._entry_px or not self._risk0 or self._risk0 <= 0:
                return
            gain_r = (float(row["close"]) - self._entry_px) / self._risk0
            if gain_r < self._trail_after_r:
                return
        high = float(row["high"])
        self._peak = high if self._peak is None else max(self._peak, high)
        atr = self._atr_at(self.i)
        if atr is None or atr <= 0:
            return
        candidate = self._peak - self._trail_atr * atr
        if self._stop is None or candidate > self._stop:
            self._stop = candidate

    def _atr_at(self, i: int) -> float | None:
        if self._atr is None:
            return None
        value = float(self._atr.iloc[i])
        return None if pd.isna(value) else value

    def _maybe_signal(self) -> None:
        if self.i < self.strategy.warmup:
            return
        signal = self.strategy.signal(self.i, self.df, self.broker.qty)
        if signal is None:
            return

        if signal.action is Action.BUY:
            if self.broker.in_position or self.risk.halted:
                return
        elif not self.broker.in_position:
            return
        self._pending = signal


def run_backtest(
    bars: list[Bar], strategy: Strategy, config: BotConfig | None = None
) -> BacktestResult:
    """Convenience wrapper: bars in, populated result out."""
    from .metrics import compute_metrics

    engine = Engine(bars_to_frame(bars), strategy, config)
    engine.run()
    result = engine.result()
    result.metrics = compute_metrics(result, bars_per_year=(config or BotConfig()).bars_per_year)
    return result
