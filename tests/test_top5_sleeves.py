"""Tests for the five top-5 sleeves (SUI, XRP, INJ, TAO, AVAX)."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy import REGISTRY, build
from bot.strategy.base import Action
from bot.strategy.swing_sleeve import SwingSleeve
from bot.strategy.wvfx_stoch import WilliamVixFixStoch


def frame(closes: list[float], *, spread: float = 0.004) -> pd.DataFrame:
    n = len(closes)
    ts = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame({
        "ts": ts,
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * (1.0 + spread),
        "low": close * (1.0 - spread),
        "close": close,
        "volume": 1.0,
    })


class RegistryTests(unittest.TestCase):
    def test_five_sleeves_are_registered(self) -> None:
        for name in (
            "swing_breakout_sui",
            "wvf_stoch_xrp",
            "swing_breakout_inj",
            "swing_breakout_tao",
            "swing_breakout_avax",
        ):
            self.assertIn(name, REGISTRY)
            self.assertEqual(build(name).name, name)

    def test_defaults_are_the_researched_numbers(self) -> None:
        sui = build("swing_breakout_sui")
        self.assertIsInstance(sui, SwingSleeve)
        self.assertEqual(
            (sui.family, sui.lookback_bars, sui.trend_bars, sui.hold_bars),
            ("breakout", 60, 120, 18),
        )
        self.assertEqual((sui.stop_atr, sui.rr, sui.trail_atr), (2.0, None, 2.5))

        inj = build("swing_breakout_inj")
        self.assertEqual((inj.lookback_bars, inj.trend_bars, inj.hold_bars), (7, 20, 3))
        self.assertEqual((inj.stop_atr, inj.trail_atr), (1.75, 3.0))

        tao = build("swing_breakout_tao")
        self.assertEqual((tao.lookback_bars, tao.trend_bars, tao.hold_bars), (5, 20, 3))
        self.assertEqual((tao.stop_atr, tao.trail_atr), (2.0, 3.0))

        avax = build("swing_breakout_avax")
        self.assertEqual((avax.lookback_bars, avax.trend_bars, avax.hold_bars), (12, 20, 3))
        self.assertEqual((avax.stop_atr, avax.trail_atr), (2.0, 3.0))

        xrp = build("wvf_stoch_xrp")
        self.assertIsInstance(xrp, WilliamVixFixStoch)
        self.assertEqual(
            (xrp.params["regime_days"], xrp.params["stop_atr"],
             xrp.params["rr"], xrp.params["max_hold"]),
            (200.0, 2.0, 2.0, 240),
        )

    def test_warmup_covers_every_window(self) -> None:
        for name in (
            "swing_breakout_sui",
            "swing_breakout_inj",
            "swing_breakout_tao",
            "swing_breakout_avax",
        ):
            s = build(name)
            self.assertGreater(s.warmup, max(s.lookback_bars, s.trend_bars))

    def test_sleeves_reject_unknown_parameters(self) -> None:
        with self.assertRaises(TypeError):
            SwingSleeve(fast_bars=3)  # type: ignore[call-arg]


class NoLookaheadTests(unittest.TestCase):
    def test_breakout_reference_excludes_the_signal_bar(self) -> None:
        closes = [1.0 + 0.1 * i / 129.0 for i in range(130)]
        closes.append(2.0)
        df = frame(closes)
        s = build("swing_breakout_sui")
        out = s.prepare(df)
        i = len(df) - 1
        expected = df["high"].iloc[i - s.lookback_bars:i].max()
        self.assertAlmostEqual(float(out["hh"].iloc[i]), float(expected))
        self.assertLess(float(out["hh"].iloc[i]), float(df["high"].iloc[i]))

    def test_breakout_signal_carries_stop_and_trail(self) -> None:
        closes = [1.0 + 0.1 * i / 129.0 for i in range(130)]
        closes.append(2.0)
        df = frame(closes)
        s = build("swing_breakout_sui")
        out = s.prepare(df)
        sig = s.signal(len(out) - 1, out, 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)
        self.assertIsNone(sig.take_profit)      # no fixed target
        self.assertEqual(sig.trail_atr, 2.5)
        self.assertLess(float(sig.stop_loss or 0.0), 2.0)

    def test_time_exit_fires_on_the_hold_bar(self) -> None:
        closes = [1.0 + 0.001 * i for i in range(30)]
        df = frame(closes)
        s = build("swing_breakout_inj")
        out = s.prepare(df)
        entry_i = 10
        s._entry_ts = out["ts"].iloc[entry_i]
        sig = s.signal(entry_i + s.hold_bars, out, 1.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.CLOSE)
        self.assertIn("time exit", sig.reason)


if __name__ == "__main__":
    unittest.main()
