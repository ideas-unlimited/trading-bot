"""Unit tests for the JUP demand-ladder sleeve."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy.base import Action
from bot.strategy.jup_liquidity_15m import JupLiquidity15m, _resample


def frame(n: int = 2) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({
        "ts": ts, "open": 1.0, "high": 1.01, "low": 0.99,
        "close": 1.0, "volume": 1.0,
    })


class ResampleTests(unittest.TestCase):
    def test_four_hour_bin_excludes_the_next_open_bar(self) -> None:
        ts = pd.date_range("2026-01-01", periods=17, freq="15min")
        df = pd.DataFrame({
            "ts": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": [float(i) for i in range(17)], "volume": 1.0,
        })
        out = _resample(df, "4h")
        # 16 bars [00:00, 04:00) are the 04:00 bar; the 04:00 bar itself is
        # the next bucket and must not leak its close into this one.
        self.assertEqual(out.index[0], pd.Timestamp("2026-01-01 04:00"))
        self.assertEqual(float(out["close"].iloc[0]), 15.0)
        self.assertEqual(out.index[1], pd.Timestamp("2026-01-01 08:00"))
        self.assertEqual(float(out["close"].iloc[1]), 16.0)


class LadderSignalTests(unittest.TestCase):
    def test_one_setup_emits_one_resting_ladder(self) -> None:
        s = JupLiquidity15m()
        setup = {
            "ts": pd.Timestamp("2026-01-01"),
            "expires_at": pd.Timestamp("2026-01-04"),
            "e1": 1.00, "mid": 0.95, "lower": 0.90, "stop": 0.85,
            "target": 1.20, "conf": 2, "tfs": ("4h", "6h"),
            "levels": (1.00, 0.95, 0.90),
        }
        s._setups = [setup]
        s._next = 0
        s._setup = None
        s._ordered = False
        s._entry_i = None
        s._first_signal_i = None
        s._had_position = False
        sig = s.signal(0, frame(), 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)
        self.assertEqual(sig.limit_prices, (1.00, 0.95, 0.90))
        self.assertAlmostEqual(sig.trail_after_price, 1.20)
        self.assertAlmostEqual(sig.size_fraction, 0.4 / 3.0)
        self.assertIsNone(s.signal(1, frame(), 0.0))  # one order at a time

    def test_time_exit_after_the_hold_window(self) -> None:
        s = JupLiquidity15m()
        setup = {
            "ts": pd.Timestamp("2026-01-01"), "expires_at": pd.Timestamp("2026-01-04"),
            "e1": 1.0, "mid": 0.95, "lower": 0.9, "stop": 0.85,
            "target": 1.2, "conf": 2, "tfs": ("4h", "6h"),
            "levels": (1.0, 0.95, 0.9),
        }
        s._setup = setup
        s._setups = [setup]
        s._next = 1
        s._ordered = True
        s._entry_i = 0
        s._first_signal_i = 0
        s._had_position = True
        sig = s.signal(480, frame(500), 1.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.CLOSE)


if __name__ == "__main__":
    unittest.main()
