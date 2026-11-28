"""Tests for the ENA breakout sleeve (trailing exit, no fixed target)."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy import REGISTRY, build
from bot.strategy.base import Action


def frame(closes: list[float], *, spread: float = 0.002) -> pd.DataFrame:
    """OHLCV frame from closes, 4h bars, tiny symmetric wicks."""
    ts = pd.date_range("2026-01-01", periods=len(closes), freq="4h")
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame({
        "ts": ts,
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * (1.0 + spread),
        "low": close * (1.0 - spread),
        "close": close,
        "volume": 1.0,
    })


def rising_then_breakout() -> pd.DataFrame:
    """160 flat bars, then a bar that closes above the prior 30-bar high."""
    closes = [1.0 + 0.0005 * i for i in range(160)]
    closes[-1] = closes[-2] * 1.05
    return frame(closes)


class EnaSleeveTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("swing_breakout_ena", REGISTRY)
        self.assertEqual(build("swing_breakout_ena").name, "swing_breakout_ena")

    def test_defaults_are_the_researched_numbers(self) -> None:
        s = build("swing_breakout_ena")
        self.assertEqual((s.family, s.lookback_bars, s.trend_bars), ("breakout", 30, 120))
        self.assertEqual((s.stop_atr, s.rr, s.trail_atr, s.hold_bars), (2.0, None, 3.0, 18))

    def test_warmup_covers_the_trend_filter(self) -> None:
        s = build("swing_breakout_ena")
        self.assertGreater(s.warmup, s.trend_bars)

    def test_breakout_buys_with_a_trail_and_no_target(self) -> None:
        df = build("swing_breakout_ena").prepare(rising_then_breakout())
        s = build("swing_breakout_ena")
        i = len(df) - 1
        row = df.iloc[i]
        self.assertGreater(float(row["close"]), float(row["hh"]))
        self.assertGreater(float(row["close"]), float(row["ema_t"]))
        sig = s.signal(i, df, 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)
        self.assertIsNone(sig.take_profit)          # no fixed target
        self.assertEqual(sig.trail_atr, 3.0)        # trailing exit instead
        expected = float(row["close"]) - 2.0 * float(row["atr"])
        self.assertAlmostEqual(float(sig.stop_loss), expected)

    def test_no_entry_below_the_prior_high(self) -> None:
        df = build("swing_breakout_ena").prepare(frame([1.0 + 0.0005 * i for i in range(160)]))
        s = build("swing_breakout_ena")
        self.assertIsNone(s.signal(len(df) - 1, df, 0.0))

    def test_time_exit_after_eighteen_bars(self) -> None:
        df = build("swing_breakout_ena").prepare(rising_then_breakout())
        s = build("swing_breakout_ena")
        n = len(df)
        s.signal(n - 1, df, 0.0)
        later = frame([1.0] * (n + 24))
        later.loc[0, "ts"] = df["ts"].iloc[n - 1]
        out = s.prepare(later)
        # the first bar with an open position is the entry bar
        self.assertIsNone(s.signal(0, out, 1.0))
        self.assertIsNone(s.signal(17, out, 1.0))
        exit_sig = s.signal(18, out, 1.0)
        self.assertIsNotNone(exit_sig)
        assert exit_sig is not None
        self.assertEqual(exit_sig.action, Action.CLOSE)


if __name__ == "__main__":
    unittest.main()