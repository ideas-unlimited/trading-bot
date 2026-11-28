"""Capitulation-dip reversion: buy a violent 4h drawdown, exit on the bounce.

Researched on UNI/USDT spot, 4h, 2024-11-28 .. 2026-11-28 (4,381 bars,
Binance production klines, next-open fills, 10 bps fee + 5 bps slippage
each side, long only):

* entry: close is at least 'drop_pct' below the prior 'window'-bar high
  (the high is shifted one bar, so the signal bar cannot create its own
  reference);
* risk: 'stop_atr' x ATR(14) below the signal close;
* target: optional 'rr' multiple of that stop distance (1.5R was chosen);
* time: close after 'max_hold' bars if neither stop nor target hit.

The measured UNI config (window=20, drop_pct=0.20, stop_atr=2.5, rr=1.5,
max_hold=18) produced +67.8% at 5% risk / 50% max position, -5.3% max
drawdown, 19 trades and 89.5% wins over the two years.  That is a small
sample from one crash-and-rebound regime, so it is a paper candidate, not a
promise.  The edge is the *event*, not a permanent uptrend: the same rule
loses in a monotonic bleed, and it sits in cash most of the time.

This is deliberately a single-symbol sleeve: the module, the class and the
registry name all carry the symbol, so nobody mistakes it for a pooled rule.
The parameters were fitted on UNI and must not be reused on another coin.
"""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy

ATR_WINDOW = 14


class CapitulationDipUni(Strategy):
    """UNI-tuned long of a sharp drawdown from the prior high."""

    name = "capitulation_dip_uni"
    PARAMS = ("window", "drop_pct", "stop_atr", "rr", "max_hold")

    def __init__(
        self,
        window: int = 20,
        drop_pct: float = 0.20,
        stop_atr: float = 2.5,
        rr: float | None = 1.5,
        max_hold: int = 18,
    ) -> None:
        if window < 2:
            raise ValueError("window must be at least 2")
        if not 0.0 < drop_pct < 1.0:
            raise ValueError("drop_pct must be in (0, 1)")
        if stop_atr <= 0:
            raise ValueError("stop_atr must be positive")
        if rr is not None and rr <= 0:
            raise ValueError("rr must be positive or None")
        if max_hold < 1:
            raise ValueError("max_hold must be at least 1")
        super().__init__(
            window=window,
            drop_pct=drop_pct,
            stop_atr=stop_atr,
            rr=rr,
            max_hold=max_hold,
        )
        self.window = window
        self.drop_pct = drop_pct
        self.stop_atr = stop_atr
        self.rr = rr
        self.max_hold = max_hold
        self.warmup = max(window, ATR_WINDOW) + 2
        self._entry_ts: pd.Timestamp | None = None

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # Shift(1): the reference high is what the market had already printed,
        # never the signal bar's own high.  This is the no-lookahead line.
        out["prior_high"] = out["high"].rolling(self.window).max().shift(1)
        out["drawdown"] = out["close"] / out["prior_high"] - 1.0
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], ATR_WINDOW)
        return out

    def reset(self) -> None:
        """Clear the entry clock before reusing this instance for a new run."""
        self._entry_ts = None

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]

        if position > 0:
            ts = row["ts"]
            if self._entry_ts is None:
                self._entry_ts = ts
            # Locate the entry bar by timestamp, not by a saved integer:
            # live polls rebuild the frame with a rolling start, so an index
            # would drift by one bar per poll.
            pos = (df["ts"] == self._entry_ts).to_numpy().nonzero()[0]
            entry_i = int(pos[0]) if len(pos) else i
            if i - entry_i >= self.max_hold:
                return Signal(
                    action=Action.CLOSE,
                    reason=f"time exit after {self.max_hold} bars",
                )
            return None

        self._entry_ts = None
        if pd.isna(row["drawdown"]) or pd.isna(row["atr"]):
            return None
        if row["drawdown"] > -self.drop_pct:
            return None

        close = float(row["close"])
        stop = close - self.stop_atr * float(row["atr"])
        risk = close - stop
        if risk <= 0:
            return None
        target = close + self.rr * risk if self.rr is not None else None
        return Signal(
            action=Action.BUY,
            reason=(
                f"capitulation {row['drawdown'] * 100:.1f}% below "
                f"{self.window}-bar high"
            ),
            stop_loss=stop,
            take_profit=target,
        )
