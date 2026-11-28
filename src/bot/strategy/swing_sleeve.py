"""Shared machinery for the swing sleeves (pullback / breakout).

Three of the per-coin sleeves in this package are the same entry logic with
different numbers, so the logic lives here once and each sleeve module fixes
its own constants.  A sleeve is a *package*: module name, class name and
registry name all carry the symbol, so a parameter set fitted on one coin can
never be mistaken for a pooled rule.

Windows are given in **bars of the sleeve's own chart**, with the research
value in hours in the docstring.  The 4h sleeves divide research hours by 4;
the 1d sleeves divide by 24.  The bar count stays correct when the deploy loop
rebuilds a shorter frame.

Both families are long-only spot and both use the same risk skeleton:

* entry at the close of the signal bar, filled at the next bar's open;
* stop `stop_atr` x ATR(14) below the signal close;
* target `rr` x that distance (a fixed-R target, not a trail);
* time exit after `hold_bars` bars if neither is hit.

The stop and target are handed to the engine, which checks them intrabar on
every later bar and scores a bar that touches both as a loss.
"""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy

ATR_WINDOW = 14


class SwingSleeve(Strategy):
    """Long-only swing entry; subclasses fix the numbers, never the logic."""

    name = "swing_sleeve"
    family = "pullback"
    #: EMA fast leg, in bars of this sleeve's chart.
    fast_bars = 6
    #: EMA trend filter, in bars of this sleeve's chart.
    trend_bars = 120
    #: Breakout lookback high, in bars.
    lookback_bars = 30
    stop_atr = 2.0
    rr: float | None = 2.0
    trail_atr: float | None = None
    #: Time exit, in bars.
    hold_bars = 30

    PARAMS: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.warmup = max(self.lookback_bars, self.trend_bars, self.fast_bars, 20) + 2
        self._entry_ts: pd.Timestamp | None = None

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], ATR_WINDOW)
        out["ema_t"] = ind.ema(out["close"], self.trend_bars)
        out["ema_f"] = ind.ema(out["close"], self.fast_bars)
        # Shift(1): the breakout reference is the high the market had already
        # printed, never the signal bar's own high.  This is the no-lookahead
        # line for the breakout family.
        out["hh"] = out["high"].rolling(self.lookback_bars).max().shift(1)
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
            # Locate the entry bar by timestamp, not by a saved integer: the
            # deploy loop rebuilds the frame with a rolling start, so an index
            # would drift by one bar per poll.
            pos = (df["ts"] == self._entry_ts).to_numpy().nonzero()[0]
            entry_i = int(pos[0]) if len(pos) else i
            if i - entry_i >= self.hold_bars:
                return Signal(
                    action=Action.CLOSE,
                    reason=f"time exit after {self.hold_bars} bars",
                )
            return None

        self._entry_ts = None
        if i < 1 or pd.isna(row["atr"]) or pd.isna(row["ema_t"]):
            return None
        close = float(row["close"])
        prev = df.iloc[i - 1]
        why = ""

        if self.family == "breakout":
            if close > float(row["hh"]) and close > float(row["ema_t"]):
                why = f"breakout over {self.lookback_bars}-bar high"
        elif self.family == "pullback":
            touched = float(prev["low"]) <= float(prev["ema_f"])
            reclaim = close > float(row["ema_f"]) and close > float(prev["close"])
            if close > float(row["ema_t"]) and touched and reclaim:
                why = f"pullback to {self.fast_bars}-bar EMA reclaimed"
        if not why:
            return None

        stop = close - self.stop_atr * float(row["atr"])
        risk = close - stop
        if risk <= 0:
            return None
        target = close + self.rr * risk if self.rr is not None else None
        return Signal(
            action=Action.BUY,
            reason=why,
            stop_loss=stop,
            take_profit=target,
            trail_atr=self.trail_atr,
        )
