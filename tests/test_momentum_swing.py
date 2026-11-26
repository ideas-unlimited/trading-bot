"""Tests for the momentum_swing strategy (no lookahead, sane signals)."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy.base import Action
from bot.strategy.momentum_swing import MomentumSwing


def frame(n: int = 320, start: float = 100.0, step: float = 0.006) -> pd.DataFrame:
    """A steadily rising market with a small pullback every 15 bars."""
    rows = []
    price = start
    for i in range(n):
        price *= 1.0 + step + (0.004 if i % 15 else -0.02)
        rows.append({
            "ts": pd.Timestamp("2023-01-01") + pd.Timedelta(days=i),
            "open": price * 0.999, "high": price * 1.02,
            "low": price * 0.985, "close": price, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


class MomentumSwingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = MomentumSwing(lookback=60, regime_ma=200)
        self.df = self.strategy.prepare(frame())

    def test_prepare_adds_columns_and_warmup(self) -> None:
        for col in ("atr", "regime", "mom", "hi20"):
            self.assertIn(col, self.df.columns)
        self.assertIsNotNone(self.df["atr"].iloc[-1])
        self.assertGreater(self.strategy.warmup, 200)

    def test_no_signal_before_warmup_values_exist(self) -> None:
        i = 100  # still inside the 200-bar regime window
        self.assertIsNone(self.strategy.signal(i, self.df, 0.0))

    def test_uptrend_entry_has_trail_and_no_hard_target(self) -> None:
        i = len(self.df) - 3
        sig = self.strategy.signal(i, self.df, 0.0)
        if sig is not None:
            self.assertEqual(sig.action, Action.BUY)
            self.assertIsNotNone(sig.stop_loss)
            self.assertLess(sig.stop_loss, float(self.df["close"].iloc[i]))
            self.assertIsNone(sig.take_profit)
            self.assertEqual(sig.trail_atr, 3.5)

    def test_optional_hard_target_still_works(self) -> None:
        strat = MomentumSwing(reward_risk=2.0)
        df = strat.prepare(frame())
        sig = strat.signal(len(df) - 3, df, 0.0)
        if sig is not None:
            self.assertIsNotNone(sig.take_profit)
            self.assertGreater(sig.take_profit, sig.stop_loss)

    def test_signal_does_not_look_ahead(self) -> None:
        i = len(self.df) - 10
        before = self.strategy.signal(i, self.df, 0.0)
        tampered = self.df.copy()
        tampered.loc[i + 1:, "close"] *= 5.0
        tampered.loc[i + 1:, "high"] *= 5.0
        after = self.strategy.signal(i, tampered, 0.0)
        self.assertEqual(before, after)

    def test_optional_exit_when_momentum_flips(self) -> None:
        strat = MomentumSwing(exit_on_momentum=True)
        df = strat.prepare(frame())
        i = len(df) - 5
        df.loc[i, "mom"] = -0.1
        sig = strat.signal(i, df, 1.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, Action.CLOSE)

    def test_in_position_default_lets_the_engine_trail(self) -> None:
        i = len(self.df) - 5
        self.assertIsNone(self.strategy.signal(i, self.df, 1.0))

    def test_conviction_is_bounded(self) -> None:
        for i in range(210, len(self.df), 10):
            score = self.strategy.conviction(self.df.iloc[i])
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_rejects_bad_parameters(self) -> None:
        with self.assertRaises(ValueError):
            MomentumSwing(lookback=2)
        with self.assertRaises(ValueError):
            MomentumSwing(atr_mult=0)
        with self.assertRaises(ValueError):
            MomentumSwing(trail_atr=0)
        with self.assertRaises(TypeError):
            MomentumSwing(nonsense=1)


if __name__ == "__main__":
    unittest.main()
