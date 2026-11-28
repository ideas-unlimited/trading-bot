"""Tests for the four per-coin swing sleeves (NEAR, RENDER, ARB, JUP)."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy import REGISTRY, build
from bot.strategy.base import Action
from bot.strategy.swing_sleeve import SwingSleeve


def frame(closes: list[float], *, spread: float = 0.004) -> pd.DataFrame:
    """OHLCV frame from closes, 4h bars, tiny symmetric wicks."""
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


def trend_then_pullback() -> pd.DataFrame:
    """150 bars up 1.00 -> 1.50, a bar that dips to the fast EMA, a reclaim."""
    closes = [1.0 + 0.5 * i / 149.0 for i in range(150)]
    closes.append(1.48)   # dips: its low pierces the 6-bar EMA
    closes.append(1.50)   # reclaims it and closes above the prior close
    return frame(closes)


class RegistryTests(unittest.TestCase):
    def test_the_four_sleeves_are_registered(self) -> None:
        for name in ("swing_pullback_near", "swing_breakout_render",
                     "swing_breakout_arb", "capitulation_dip_jup"):
            self.assertIn(name, REGISTRY)
            self.assertEqual(build(name).name, name)

    def test_defaults_are_the_researched_numbers(self) -> None:
        near = build("swing_pullback_near")
        self.assertEqual((near.family, near.fast_bars, near.trend_bars, near.hold_bars),
                         ("pullback", 6, 120, 30))
        self.assertEqual((near.stop_atr, near.rr), (2.0, 2.0))

        render = build("swing_breakout_render")
        self.assertEqual((render.family, render.lookback_bars, render.trend_bars),
                         ("breakout", 30, 120))
        self.assertEqual((render.stop_atr, render.rr, render.hold_bars), (1.5, 2.0, 18))

        arb = build("swing_breakout_arb")
        self.assertEqual((arb.family, arb.lookback_bars, arb.hold_bars), ("breakout", 42, 18))

        jup = build("capitulation_dip_jup")
        self.assertEqual((jup.window, jup.drop_pct), (60, 0.20))
        self.assertEqual((jup.stop_atr, jup.rr, jup.max_hold), (2.0, 1.5, 30))

    def test_warmup_covers_every_window(self) -> None:
        for name in ("swing_pullback_near", "swing_breakout_render", "swing_breakout_arb"):
            s = build(name)
            self.assertGreater(s.warmup, max(s.lookback_bars, s.trend_bars))

    def test_sleeves_reject_unknown_parameters(self) -> None:
        with self.assertRaises(TypeError):
            SwingSleeve(fast_bars=3)  # type: ignore[call-arg]


class NoLookaheadTests(unittest.TestCase):
    def test_breakout_reference_excludes_the_signal_bar(self) -> None:
        df = trend_then_pullback()
        s = build("swing_breakout_render")
        out = s.prepare(df)
        i = 100
        expected = df["high"].iloc[i - s.lookback_bars:i].max()
        self.assertAlmostEqual(float(out["hh"].iloc[i]), float(expected))
        self.assertLess(float(out["hh"].iloc[i]), float(df["high"].iloc[i]))

    def test_indicators_are_prefix_stable(self) -> None:
        df = trend_then_pullback()
        full = build("swing_pullback_near").prepare(df)
        part = build("swing_pullback_near").prepare(df.iloc[:120].copy())
        for col in ("atr", "ema_t", "ema_f", "hh"):
            pd.testing.assert_series_equal(
                full[col].iloc[:120], part[col], check_names=False, check_freq=False
            )


class SignalTests(unittest.TestCase):
    def test_pullback_buys_the_reclaim(self) -> None:
        df = build("swing_pullback_near").prepare(trend_then_pullback())
        s = build("swing_pullback_near")
        i = len(df) - 1
        prev = df.iloc[i - 1]
        row = df.iloc[i]
        # the frame really is a pullback: prior low under the fast EMA, the
        # signal bar back above it, above the prior close and above trend
        self.assertLessEqual(float(prev["low"]), float(prev["ema_f"]))
        self.assertGreater(float(row["close"]), float(row["ema_f"]))
        self.assertGreater(float(row["close"]), float(prev["close"]))
        self.assertGreater(float(row["close"]), float(row["ema_t"]))

        sig = s.signal(i, df, 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)
        atr = float(row["atr"])
        self.assertAlmostEqual(float(sig.stop_loss), float(row["close"]) - 2.0 * atr)
        self.assertAlmostEqual(float(sig.take_profit), float(row["close"]) + 4.0 * atr)

    def test_breakout_buys_a_new_high_above_trend(self) -> None:
        closes = [1.0 + 0.3 * i / 199.0 for i in range(200)]
        df = frame(closes)
        s = build("swing_breakout_render")
        prepared = s.prepare(df)
        prior_high = float(prepared["hh"].iloc[-1])
        df.loc[df.index[-1], ["close", "high"]] = prior_high * 1.02
        prepared = s.prepare(df)
        sig = s.signal(len(prepared) - 1, prepared, 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)

    def test_breakout_does_not_buy_a_high_below_trend(self) -> None:
        closes = [2.0 - 0.5 * i / 149.0 for i in range(150)] + [1.62]
        df = frame(closes)
        s = build("swing_breakout_render")
        prepared = s.prepare(df)
        i = len(prepared) - 1
        self.assertGreater(float(prepared["close"].iloc[i]), float(prepared["hh"].iloc[i]))
        self.assertLess(float(prepared["close"].iloc[i]), float(prepared["ema_t"].iloc[i]))
        self.assertIsNone(s.signal(i, prepared, 0.0))

    def test_jup_buys_a_twenty_percent_capitulation(self) -> None:
        closes = [1.0] * 200 + [0.79]
        df = frame(closes, spread=0.001)
        s = build("capitulation_dip_jup")
        prepared = s.prepare(df)
        i = len(prepared) - 1
        self.assertLessEqual(float(prepared["drawdown"].iloc[i]), -0.20)
        sig = s.signal(i, prepared, 0.0)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, Action.BUY)

    def test_jup_ignores_a_shallow_dip(self) -> None:
        closes = [1.0] * 200 + [0.90]
        df = frame(closes, spread=0.001)
        s = build("capitulation_dip_jup")
        prepared = s.prepare(df)
        self.assertIsNone(s.signal(len(prepared) - 1, prepared, 0.0))

    def test_time_exit_fires_after_the_hold_window(self) -> None:
        for name, hold in (("swing_pullback_near", 30), ("swing_breakout_arb", 18),
                           ("capitulation_dip_jup", 30)):
            df = frame([1.0] * 60)
            s = build(name)
            prepared = s.prepare(df)
            s._entry_ts = prepared["ts"].iloc[-1 - hold]
            sig = s.signal(len(prepared) - 1, prepared, 1.0)
            self.assertIsNotNone(sig, name)
            assert sig is not None
            self.assertEqual(sig.action, Action.CLOSE, name)
            # one bar earlier it is still holding
            s._entry_ts = prepared["ts"].iloc[-hold]
            self.assertIsNone(s.signal(len(prepared) - 1, prepared, 1.0), name)
