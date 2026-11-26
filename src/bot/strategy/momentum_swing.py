"""Momentum swing: buy strength in an uptrend, let a trailing stop end it.

Research result this encodes (5 coins, 2017-2026, 162 trades):

* entry  - close above its 200-bar average, 60-bar momentum positive, and
           price within 2% of the prior 20-bar high;
* stop   - 2.5 ATR below the next open;
* exit   - a 3.5 ATR Chandelier trail from the highest high since entry.
           No hard target.  With a fixed 1.2R target the same entries win
           58% of the time and compound far worse (max drawdown -65% vs
           -22%), because the target caps exactly the trades that pay.

Trailing does NOT raise the win rate: it stays near 47-48%.  It raises the
size of the average winner.  Median capture is about 44% of the trade's
best unrealised profit - giving back half the peak is the price of the tail
trades that return 5-15R.
"""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Action, Signal, Strategy


class MomentumSwing(Strategy):
    """Long-only momentum breakout with an ATR trailing stop.

    Parameters
    ----------
    lookback    : bars of momentum that must be positive (default 60).
    regime_ma   : trend average; entry needs close above it (default 200).
    atr_mult    : initial stop distance in ATRs (default 2.5).
    trail_atr   : Chandelier trail distance in ATRs (default 3.5).  The
                  researched plateau is 3.0-4.0; 2.0 churns and loses.
    reward_risk : optional hard target in R.  None (default) means the
                  trail decides the exit.
    breakout    : max distance below the prior 20-bar high (default 2%).

    conviction is a transparent 0-100 state score, not a learned
    probability: trend (0-40), momentum (0-30), breakout freshness (0-30).
    Learned probabilities did not generalise out of sample (AUC 0.49/0.36).
    """

    name = "momentum_swing"
    PARAMS = (
        "lookback", "regime_ma", "atr_mult", "trail_atr", "reward_risk",
        "breakout", "exit_on_momentum", "exit_on_regime",
    )

    def __init__(
        self,
        lookback: int = 60,
        regime_ma: int = 200,
        atr_mult: float = 2.5,
        trail_atr: float | None = 3.5,
        reward_risk: float | None = None,
        breakout: float = 0.02,
        exit_on_momentum: bool = False,
        exit_on_regime: bool = False,
    ) -> None:
        if lookback < 5:
            raise ValueError("lookback must be >= 5")
        if atr_mult <= 0:
            raise ValueError("atr_mult must be positive")
        if trail_atr is not None and trail_atr <= 0:
            raise ValueError("trail_atr must be positive or None")
        if reward_risk is not None and reward_risk <= 0:
            raise ValueError("reward_risk must be positive or None")
        super().__init__(
            lookback=lookback, regime_ma=regime_ma, atr_mult=atr_mult,
            trail_atr=trail_atr, reward_risk=reward_risk, breakout=breakout,
            exit_on_momentum=exit_on_momentum, exit_on_regime=exit_on_regime,
        )
        self.warmup = max(lookback, regime_ma, 20) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        close = out["close"]
        out["atr"] = ind.atr(out["high"], out["low"], close, 14)
        out["regime"] = close.rolling(self.regime_ma).mean()
        out["mom"] = close / close.shift(self.lookback) - 1.0
        # Prior 20-bar high: shifted so the signal bar cannot see itself.
        out["hi20"] = out["high"].rolling(20).max().shift(1)
        return out

    def conviction(self, row: pd.Series) -> int:
        """0-100 state score (trend 40, momentum 30, breakout 30)."""
        score = 0.0
        if pd.isna(row["regime"]) or pd.isna(row["mom"]) or pd.isna(row["hi20"]):
            return 0
        trend = float(row["close"]) / float(row["regime"]) - 1.0
        score += max(0.0, min(1.0, trend / 0.15)) * 40.0
        score += max(0.0, min(1.0, float(row["mom"]) / 0.40)) * 30.0
        if float(row["hi20"]) > 0:
            gap = 1.0 - float(row["close"]) / float(row["hi20"])
            score += max(0.0, min(1.0, 1.0 - gap / max(self.breakout * 3, 1e-9))) * 30.0
        return int(round(score))

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if pd.isna(row["atr"]) or pd.isna(row["regime"]) or pd.isna(row["mom"]):
            return None
        close = float(row["close"])
        atr = float(row["atr"])

        if position > 0:
            if self.exit_on_regime and close < float(row["regime"]):
                return Signal(Action.CLOSE, "lost regime MA")
            if self.exit_on_momentum and float(row["mom"]) < 0:
                return Signal(Action.CLOSE, "momentum flipped negative")
            # Otherwise the engine owns the exit: the trailing stop.
            return None

        if close <= float(row["regime"]) or float(row["mom"]) <= 0:
            return None
        gap = 1.0 - close / float(row["hi20"]) if float(row["hi20"]) > 0 else 1.0
        if gap > self.breakout:
            return None

        stop = close - self.atr_mult * atr
        if stop <= 0:
            return None
        target = close + self.reward_risk * self.atr_mult * atr if self.reward_risk else None
        conv = self.conviction(row)
        return Signal(
            action=Action.BUY,
            reason=f"momentum {float(row['mom']) * 100:.0f}% | breakout {1 - gap:.1%} | conviction {conv}",
            stop_loss=stop,
            take_profit=target,
            trail_atr=self.trail_atr,
        )
