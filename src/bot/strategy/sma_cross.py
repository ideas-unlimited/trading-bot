"""A trend-following moving-average crossover with an ATR stop."""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy

ATR_WINDOW = 14


class SmaCross(Strategy):
    """Long when fast SMA is above slow SMA, flat otherwise.

    Stop loss sits ``atr_mult`` ATRs below the entry price, so position size
    adapts to volatility (see ``RiskManager``).
    """

    name = "sma_cross"
    PARAMS = ("fast", "slow", "atr_mult")

    def __init__(self, fast: int = 20, slow: int = 50, atr_mult: float = 2.5) -> None:
        if fast >= slow:
            raise ValueError("fast window must be smaller than slow window")
        super().__init__(fast=fast, slow=slow, atr_mult=atr_mult)
        self.fast = fast
        self.slow = slow
        self.atr_mult = atr_mult
        self.warmup = max(slow, ATR_WINDOW) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["fast_ma"] = ind.sma(out["close"], self.fast)
        out["slow_ma"] = ind.sma(out["close"], self.slow)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], ATR_WINDOW)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row["slow_ma"]) or pd.isna(prev["slow_ma"]) or pd.isna(row["atr"]):
            return None

        crossed_up = prev["fast_ma"] <= prev["slow_ma"] and row["fast_ma"] > row["slow_ma"]
        crossed_down = prev["fast_ma"] >= prev["slow_ma"] and row["fast_ma"] < row["slow_ma"]

        if position == 0 and crossed_up:
            return Signal(
                action=Action.BUY,
                reason="fast SMA crossed above slow SMA",
                stop_loss=float(row["close"] - self.atr_mult * row["atr"]),
            )
        if position > 0 and crossed_down:
            return Signal(action=Action.CLOSE, reason="fast SMA crossed below slow SMA")
        return None
