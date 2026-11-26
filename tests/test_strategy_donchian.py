"""Tests for the Donchian breakout strategy (no lookahead, correct exits)."""

import unittest

import numpy as np
import pandas as pd

from bot.strategy.donchian import DonchianBreakout


def _frame(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="h"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * n,
    })


class DonchianTests(unittest.TestCase):
    def _strategy(self, **params):
        s = DonchianBreakout(**params)
        return s

    def test_warmup_blocks_early_signals(self) -> None:
        s = self._strategy(entry_window=5, exit_window=2)
        closes = [100.0] * 8 + [106.0] * 3
        df = s.prepare(_frame(closes))
        for i in range(6):
            self.assertIsNone(s.signal(i, df, 0))

    def test_buys_on_breakout_above_prior_high(self) -> None:
        s = self._strategy(entry_window=5, exit_window=2, atr_mult=2.0)
        closes = [100.0] * 19 + [106.0]
        df = s.prepare(_frame(closes))
        sig = s.signal(19, df, 0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action.value, "buy")
        self.assertLess(sig.stop_loss, 106.0)

    def test_no_lookahead_current_high_not_in_level(self) -> None:
        # a single spike bar must NOT trigger on itself
        s = self._strategy(entry_window=5, exit_window=2)
        closes = [100.0] * 10
        highs = [100.5] * 10
        df = s.prepare(_frame(closes, highs=highs, lows=[99.5] * 10))
        self.assertIsNone(s.signal(9, df, 0))

    def test_closes_on_breakdown(self) -> None:
        s = self._strategy(entry_window=5, exit_window=3, atr_mult=2.0)
        closes = [100.0] * 18 + [106.0, 95.0]
        highs = [101.0] * 18 + [107.0, 96.0]
        lows = [99.0] * 18 + [105.0, 94.0]
        df = s.prepare(_frame(closes, highs=highs, lows=lows))
        self.assertIsNone(s.signal(18, df, 1.0))  # breakout bar, still above exit level
        sig = s.signal(19, df, 1.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action.value, "close")

    def test_window_validation(self) -> None:
        with self.assertRaises(ValueError):
            DonchianBreakout(entry_window=5, exit_window=5)

    def test_registry_backtest_runs(self) -> None:
        from bot.backtest import run_backtest
        from bot.config import BotConfig
        from bot.data.feed import Bar
        from bot.strategy import build

        rng = np.random.default_rng(3)
        price, bars = 100.0, []
        for i in range(300):
            price *= 1 + rng.normal(0.0005, 0.01)
            c = price
            bars.append(Bar(ts=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime() + pd.Timedelta(hours=4 * i),
                            open=c * 0.999, high=c * 1.01, low=c * 0.985, close=c, volume=1000))
        result = run_backtest(bars, build("donchian", entry_window=20, exit_window=10), BotConfig())
        self.assertGreater(len(result.equity_curve), 0)


if __name__ == "__main__":
    unittest.main()
