"""Tests for the UNI capitulation-dip sleeve."""

import unittest

import pandas as pd

from bot.backtest import Engine
from bot.config import BotConfig
from bot.strategy import REGISTRY, build
from bot.strategy.base import Action


def frame(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs if highs is not None else [c * 1.01 for c in closes]
    lows = lows if lows is not None else [c * 0.99 for c in closes]
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


class TestCapitulationDipUni(unittest.TestCase):
    def test_registered_and_defaults(self):
        s = build("capitulation_dip_uni")
        self.assertIn("capitulation_dip_uni", REGISTRY)
        self.assertEqual(s.params["window"], 20)
        self.assertEqual(s.params["drop_pct"], 0.20)
        self.assertEqual(s.params["stop_atr"], 2.5)
        self.assertEqual(s.params["rr"], 1.5)
        self.assertEqual(s.params["max_hold"], 18)
        self.assertIsNone(s.params["trail_atr"])
        self.assertEqual(s.params["trail_after_r"], 0.0)

    def test_invalid_parameters_raise(self):
        for kwargs in (
            {"window": 1},
            {"drop_pct": 0.0},
            {"drop_pct": 1.0},
            {"stop_atr": 0.0},
            {"rr": 0.0},
            {"max_hold": 0},
            {"trail_atr": 0.0},
            {"trail_after_r": -0.1},
        ):
            with self.assertRaises(ValueError):
                build("capitulation_dip_uni", **kwargs)

    def test_buys_after_sharp_drawdown(self):
        closes = [100.0] * 40 + [70.0]
        s = build("capitulation_dip_uni")
        df = s.prepare(frame(closes))
        sig = s.signal(len(df) - 1, df, 0.0)
        self.assertIsNotNone(sig)
        self.assertIs(sig.action, Action.BUY)
        self.assertLess(sig.stop_loss, df["close"].iloc[-1])
        self.assertGreater(sig.take_profit, df["close"].iloc[-1])

    def test_trail_is_carried_on_the_buy_signal(self):
        closes = [100.0] * 40 + [70.0]
        s = build("capitulation_dip_uni", trail_atr=2.0, trail_after_r=1.0)
        df = s.prepare(frame(closes))
        sig = s.signal(len(df) - 1, df, 0.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.trail_atr, 2.0)
        self.assertEqual(sig.trail_after_r, 1.0)

    def test_does_not_buy_shallow_dip(self):
        closes = [100.0] * 40 + [95.0]
        s = build("capitulation_dip_uni")
        df = s.prepare(frame(closes))
        self.assertIsNone(s.signal(len(df) - 1, df, 0.0))

    def test_prior_high_excludes_signal_bar(self):
        # The last bar prints a huge high but closes above the old high:
        # the drawdown must be measured against the prior 100, not 200.
        closes = [100.0] * 40 + [150.0]
        highs = [101.0] * 40 + [200.0]
        lows = [99.0] * 40 + [149.0]
        s = build("capitulation_dip_uni")
        df = s.prepare(frame(closes, highs, lows))
        self.assertEqual(df["prior_high"].iloc[-1], 101.0)
        self.assertGreater(df["drawdown"].iloc[-1], 0.0)
        self.assertIsNone(s.signal(len(df) - 1, df, 0.0))

    def test_time_exit_after_max_hold(self):
        closes = [100.0] * 40 + [70.0] + [72.0] * 30
        s = build("capitulation_dip_uni", max_hold=5)
        df = s.prepare(frame(closes))
        i = 40
        self.assertIsNotNone(s.signal(i, df, 0.0))
        self.assertIsNone(s.signal(i, df, 1.0))  # sets the entry bar
        exit_sig = s.signal(i + 5, df, 1.0)
        self.assertIsNotNone(exit_sig)
        self.assertIs(exit_sig.action, Action.CLOSE)

    def test_prepare_is_prefix_stable(self):
        closes = [100 + (i % 7) for i in range(80)]
        s = build("capitulation_dip_uni")
        full = s.prepare(frame(closes))
        part = s.prepare(frame(closes[:50]))
        for col in ("prior_high", "drawdown", "atr"):
            self.assertAlmostEqual(
                float(full[col].iloc[49]), float(part[col].iloc[49]), places=10
            )

    def test_reset_clears_entry_state(self):
        s = build("capitulation_dip_uni")
        df = s.prepare(frame([100.0] * 40 + [70.0]))
        s.signal(len(df) - 1, df, 0.0)
        s._entry_ts = df["ts"].iloc[0]
        s.reset()
        self.assertIsNone(s._entry_ts)

    def test_rolling_window_keeps_entry_clock(self):
        closes = [100.0] * 40 + [70.0] + [72.0] * 30
        s = build("capitulation_dip_uni", max_hold=5)
        full = s.prepare(frame(closes))
        i = 40
        self.assertIsNotNone(s.signal(i, full, 0.0))
        self.assertIsNone(s.signal(i, full, 1.0))
        # A live poll rebuilds the frame with one old bar dropped.  The entry
        # timestamp must still resolve to the same bar, not drift by one.
        shifted = frame(closes[1:])
        shifted["ts"] = full["ts"].iloc[1:].reset_index(drop=True)
        shifted = s.prepare(shifted)
        self.assertIsNone(s.signal(i - 1, shifted, 1.0))
        self.assertIsNone(s.signal(i, shifted, 1.0))
        exit_sig = s.signal(i + 4, shifted, 1.0)
        self.assertIsNotNone(exit_sig)
        self.assertIs(exit_sig.action, Action.CLOSE)

    def test_engine_takes_a_profitable_bounce(self):
        closes = [100.0] * 40 + [70.0] + [72.0 + i for i in range(25)]
        df = frame(closes)
        engine = Engine(df, build("capitulation_dip_uni"), BotConfig(initial_cash=1000))
        engine.run()
        res = engine.result()
        self.assertGreaterEqual(len(res.trades), 1)
        self.assertGreater(res.trades[0].pnl, 0)


if __name__ == "__main__":
    unittest.main()
