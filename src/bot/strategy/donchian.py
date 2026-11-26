"""Donchian channel breakout: buy N-bar-high breakouts, exit on M-bar lows."""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy


class DonchianBreakout(Strategy):
    """Enter when close breaks above the prior ``entry_window``-bar high.

    Exits when close breaks below the prior ``exit_window``-bar low, or at
    the ATR stop.  The channel levels are *shifted by one bar* so the signal
    can never see the breakout bar's own high — no lookahead, like every
    strategy in this project.
    """

    name = "donchian"
    PARAMS = ("entry_window", "exit_window", "atr_mult")

    def __init__(
        self,
        entry_window: int = 55,
        exit_window: int = 10,
        atr_mult: float = 3.0,
    ) -> None:
        if entry_window <= exit_window:
            raise ValueError("entry_window must be larger than exit_window")
        super().__init__(entry_window=entry_window, exit_window=exit_window, atr_mult=atr_mult)
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.atr_mult = atr_mult
        self.warmup = max(entry_window, exit_window, 14) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # shift(1): levels exclude the current bar (no self-referencing breakout)
        out["entry_level"] = out["high"].rolling(self.entry_window).max().shift(1)
        out["exit_level"] = out["low"].rolling(self.exit_window).min().shift(1)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if pd.isna(row["entry_level"]) or pd.isna(row["atr"]):
            return None

        if position == 0 and row["close"] > row["entry_level"]:
            return Signal(
                action=Action.BUY,
                reason=f"breakout above {self.entry_window}-bar high",
                stop_loss=float(row["close"] - self.atr_mult * row["atr"]),
            )
        if (
            position > 0
            and row["exit_level"] == row["exit_level"]  # not NaN
            and row["close"] < row["exit_level"]
        ):
            return Signal(
                action=Action.CLOSE,
                reason=f"breakdown below {self.exit_window}-bar low",
            )
        return None
