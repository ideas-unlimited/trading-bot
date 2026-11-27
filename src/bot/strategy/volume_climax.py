"""Volume-climax reversal: capitulation volume at a discount, long the absorption.

Port of the setup shown in the shared short "This Indicator PREDICTS Market
Bottoms with HIGH Accuracy" (Tarzan Trading TT, Facebook, 2026-11-26): after a
decline, an abnormal volume bar that still closes in the upper half of its
range is read as absorption -- sellers feeding into resting demand -- and the
next bar's open is the long entry.  Stop sits under the climax bar, target is a
multiple of that risk; the optional ATR trail covers the runners.

Everything here is causal.  The volume z-score compares a bar with the window
that *precedes* it (shifted one bar), the drawdown reference is the prior
``lookback``-bar high, and the engine fills signals at the next open.
"""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy


class VolumeClimax(Strategy):
    """Buy absorption bars on capitulation volume after a decline."""

    name = "vol_climax"
    PARAMS = (
        "vol_window",
        "vol_z",
        "lookback",
        "drop_pct",
        "close_pos",
        "rr",
        "stop_atr",
        "max_hold",
        "trail_atr",
    )

    def __init__(
        self,
        vol_window: int = 20,
        vol_z: float = 2.0,
        lookback: int = 20,
        drop_pct: float = 0.06,
        close_pos: float = 0.5,
        rr: float = 2.0,
        stop_atr: float = 0.5,
        max_hold: int = 20,
        trail_atr: float | None = None,
    ) -> None:
        if vol_window < 3:
            raise ValueError("vol_window must be at least 3")
        if lookback < 2:
            raise ValueError("lookback must be at least 2")
        if not 0.0 <= close_pos <= 1.0:
            raise ValueError("close_pos must be in [0, 1]")
        if drop_pct < 0:
            raise ValueError("drop_pct must be >= 0")
        if rr <= 0 or max_hold < 1 or stop_atr < 0:
            raise ValueError("rr/max_hold must be positive, stop_atr >= 0")
        super().__init__(
            vol_window=vol_window,
            vol_z=vol_z,
            lookback=lookback,
            drop_pct=drop_pct,
            close_pos=close_pos,
            rr=rr,
            stop_atr=stop_atr,
            max_hold=max_hold,
            trail_atr=trail_atr,
        )
        self._entry_i: int | None = None
        self.warmup = max(vol_window, lookback, 14) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        vol = out["volume"].astype(float)
        base = vol.rolling(self.vol_window).mean().shift(1)
        spread = vol.rolling(self.vol_window).std(ddof=0).shift(1)
        # Flat volume windows have zero dispersion: floor it at 1% of the window
        # mean so a genuine spike still scores instead of dividing by zero (and
        # a dead-flat tape scores 0 rather than +/-inf).
        spread = spread.where(spread > 0, base.abs() * 0.01).replace(0.0, float("nan"))
        out["vol_z"] = (vol - base) / spread

        # Reference high excludes the current bar: a decline is measured against
        # what came before, never against a high the bar itself printed.
        out["prior_high"] = out["high"].rolling(self.lookback).max().shift(1)
        out["drawdown"] = out["close"] / out["prior_high"] - 1.0

        rng = (out["high"] - out["low"]).replace(0.0, float("nan"))
        out["close_pos"] = ((out["close"] - out["low"]) / rng).clip(0.0, 1.0)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]

        if position > 0:
            if self._entry_i is None:
                self._entry_i = i
            if i - self._entry_i >= self.max_hold:
                return Signal(
                    action=Action.CLOSE,
                    reason=f"time exit after {self.max_hold} bars",
                )
            return None

        self._entry_i = None
        if pd.isna(row["vol_z"]) or pd.isna(row["atr"]) or pd.isna(row["drawdown"]):
            return None

        climax = (
            row["vol_z"] >= self.vol_z
            and row["drawdown"] <= -self.drop_pct
            and row["close_pos"] >= self.close_pos
        )
        if not climax:
            return None

        close = float(row["close"])
        stop = float(min(row["low"], close) - self.stop_atr * float(row["atr"]))
        risk = close - stop
        if risk <= 0:
            return None
        return Signal(
            action=Action.BUY,
            reason=(
                f"volume climax z={row['vol_z']:.1f} at "
                f"{row['drawdown'] * 100:.1f}% below {self.lookback}-bar high"
            ),
            stop_loss=stop,
            take_profit=close + self.rr * risk,
            trail_atr=self.trail_atr,
        )
