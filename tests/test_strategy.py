import unittest

import pandas as pd

from bot.data.feed import Bar
from bot.strategy import REGISTRY, build
from bot.strategy.base import Action, Strategy


def frame(closes):
    """Build a minimal OHLCV dataframe from a close-price list."""
    n = len(closes)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1.0] * n,
        }
    )


class TestRegistry(unittest.TestCase):
    def test_build_known_strategy(self):
        s = build("sma_cross", fast=5, slow=10)
        self.assertEqual(s.name, "sma_cross")
        self.assertEqual(s.params, {"fast": 5, "slow": 10, "atr_mult": 2.5})

    def test_unknown_strategy_raises(self):
        with self.assertRaises(KeyError):
            build("does_not_exist")

    def test_unknown_parameter_raises(self):
        with self.assertRaises(TypeError):
            build("sma_cross", faast=5)

    def test_bad_parameter_values_raise(self):
        with self.assertRaises(ValueError):
            build("sma_cross", fast=50, slow=20)
        with self.assertRaises(ValueError):
            build("rsi_meanrev", entry=60, exit_level=40)

    def test_registry_entries_are_strategies(self):
        for cls in REGISTRY.values():
            self.assertTrue(issubclass(cls, Strategy))


class TestSmaCross(unittest.TestCase):
    def _run(self, closes, **params):
        s = build("sma_cross", fast=3, slow=6, **params)
        df = s.prepare(frame(closes))
        out = []
        for i in range(len(df)):
            sig = s.signal(i, df, position=0.0)
            if sig:
                out.append((i, sig))
        return s, df, out

    def test_no_signal_during_warmup(self):
        _, _, out = self._run([100.0] * 30)
        self.assertEqual(out, [])

    def test_buy_signal_on_cross_up(self):
        closes = [10.0] * 6 + [9.6, 9.2, 8.8, 8.4, 8.0, 7.8, 7.9, 8.1, 8.4, 8.8, 9.2, 9.6, 10.0]
        closes += [10.0 + 0.5 * i for i in range(10)]
        s, df, out = self._run(closes)
        buys = [o for o in out if o[1].action is Action.BUY]
        self.assertTrue(buys, "expected at least one buy")
        i, sig = buys[0]
        self.assertIsNotNone(sig.stop_loss)
        self.assertLess(sig.stop_loss, df.iloc[i]["close"])

    def test_close_signal_only_when_in_position(self):
        closes = [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9]
        s, df, _ = self._run(closes)
        for i in range(len(df)):
            for position in (0.0, 1.0):
                sig = s.signal(i, df, position)
                if sig and sig.action is Action.CLOSE:
                    self.assertGreater(position, 0)

    def test_prepare_adds_columns(self):
        s, df, _ = self._run([float(i) for i in range(1, 30)])
        for col in ("fast_ma", "slow_ma", "atr"):
            self.assertIn(col, df.columns)


class TestRsiMeanReversion(unittest.TestCase):
    def test_enters_oversold_pullback_in_uptrend(self):
        closes = [100 + i * 1.0 for i in range(120)]
        closes += [closes[-1] - i * 1.2 for i in range(1, 15)]  # sharp dip
        s = build("rsi_meanrev", rsi_window=5, entry=40, exit_level=55, trend_window=100)
        df = s.prepare(frame(closes))
        signals = [(i, s.signal(i, df, 0.0)) for i in range(len(df))]
        buys = [sig for _, sig in signals if sig and sig.action is Action.BUY]
        self.assertTrue(buys, "a steep pullback in an uptrend should trigger an entry")
        self.assertIsNotNone(buys[0].stop_loss)

    def test_does_not_buy_downtrend_oversold(self):
        closes = [200 - i * 1.0 for i in range(200)]
        s = build("rsi_meanrev", rsi_window=5, entry=45, exit_level=60, trend_window=50)
        df = s.prepare(frame(closes))
        buys = [s.signal(i, df, 0.0) for i in range(len(df))]
        self.assertFalse(any(b and b.action is Action.BUY for b in buys))


if __name__ == "__main__":
    unittest.main()
