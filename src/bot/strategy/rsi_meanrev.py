"""A mean-reversion entry: buy oversold RSI inside an uptrend, exit on pop."""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy

ATR_WINDOW = 14


class RsiMeanReversion(Strategy):
    """Enter when RSI drops below ``entry`` while price is above the trend SMA."""

    name = "rsi_meanrev"
    PARAMS = ("rsi_window", "entry", "exit_level", "trend_window", "atr_mult")

    def __init__(
        self,
        rsi_window: int = 14,
        entry: float = 30.0,
        exit_level: float = 55.0,
        trend_window: int = 200,
        atr_mult: float = 2.0,
    ) -> None:
        if not 0 < entry < exit_level < 100:
            raise ValueError("need 0 < entry < exit_level < 100")
        super().__init__(
            rsi_window=rsi_window,
            entry=entry,
            exit_level=exit_level,
            trend_window=trend_window,
            atr_mult=atr_mult,
        )
        self.rsi_window = rsi_window
        self.entry = entry
        self.exit_level = exit_level
        self.trend_window = trend_window
        self.atr_mult = atr_mult
        self.warmup = max(rsi_window, trend_window, ATR_WINDOW) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["rsi"] = ind.rsi(out["close"], self.rsi_window)
        out["trend_ma"] = ind.sma(out["close"], self.trend_window)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], ATR_WINDOW)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row["rsi"]) or pd.isna(prev["rsi"]) or pd.isna(row["trend_ma"]):
            return None
        if pd.isna(row["atr"]):
            return None

        if position == 0:
            flipped = prev["rsi"] >= self.entry and row["rsi"] < self.entry
            in_uptrend = row["close"] > row["trend_ma"]
            if flipped and in_uptrend:
                return Signal(
                    action=Action.BUY,
                    reason=f"RSI {row['rsi']:.1f} oversold in uptrend",
                    stop_loss=float(row["close"] - self.atr_mult * row["atr"]),
                )
            return None

        if row["rsi"] > self.exit_level:
            return Signal(action=Action.CLOSE, reason=f"RSI {row['rsi']:.1f} recovered")
        return None
