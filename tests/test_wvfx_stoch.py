"""Tests for the William_Vix_Fix_Stoch strategy and its indicator ports."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from bot.analysis import stochastic, williams_vix_fix
from bot.strategy import build
from bot.strategy.wvfx_stoch import WilliamVixFixStoch


def make_frame(n: int = 120, crash: int = 80) -> pd.DataFrame:
    """Sideways ~100, a short slide, then a hammer bar and a bounce.

    With ``percentile_high=1.01`` (out-of-range on purpose) only the
    Bollinger band can turn WVF green.  The slide prints wvf 6/11/16, the
    hammer prints 30 and is the first green bar after a non-green bar.
    %K at the hammer is 19.4 (<20) and crosses above %D -> the one entry.
    """
    rows = []
    lows = [99.5, 99.0, 99.4, 99.1, 99.3, 99.6]  # deterministic wobble
    for i in range(n):
        if i < crash - 3:
            close = 100.0
            o, hi, lo, vol = 100.0, 100.9, lows[i % len(lows)], 100.0
        elif i < crash:
            close = 100.0 - (i - (crash - 3) + 1) * 5.0  # 95, 90, 85
            o, hi, lo, vol = close + 1.0, close + 1.5, close - 1.0, 300.0
        elif i == crash:
            o, hi, lo, close, vol = 84.0, 85.0, 70.0, 76.0, 5000.0
        else:
            j = i - crash
            close = 76.0 + 2.0 * j
            o, hi, lo, vol = close - 1.0, close + 1.0, 70.0 + 1.0 * j, 150.0
        rows.append(dict(open=o, high=hi, low=lo, close=close, volume=vol))
    df = pd.DataFrame(rows)
    df.insert(0, "ts", pd.date_range("2026-01-01", periods=len(df), freq="4h"))
    return df


def flat_frame(n: int = 120) -> pd.DataFrame:
    """Same sideways market with no crash: no setup can fire."""
    return make_frame(n, crash=n)  # the crash branch is never reached


SAFE = dict(percentile_high=1.01, band_mult=3.5, regime_days=0)


class IndicatorTests(unittest.TestCase):
    def test_wvf_value_sanity(self):
        w = williams_vix_fix(make_frame(), percentile_high=1.01)
        self.assertTrue(w["wvf"].dropna().between(0, 100).all())
        # hammer bar: low 70 vs highest close 100 over 22 bars -> wvf = 30
        self.assertAlmostEqual(w["wvf"].iloc[80], 30.0, places=6)
        self.assertTrue(bool(w["green"].iloc[80]))
        green = w["green"].fillna(False)
        consistent = (w["wvf"] >= w["upper_band"]) | (w["wvf"] >= w["range_high"])
        self.assertTrue((green == (consistent & w["wvf"].notna()
                                   & w["upper_band"].notna()
                                   & w["range_high"].notna())).all())

    def test_wvf_green_via_percentile_band(self):
        df = make_frame()
        w = williams_vix_fix(df, percentile_high=0.85)
        expected_rh = 0.85 * w["wvf"].rolling(50, min_periods=50).max()
        pd.testing.assert_series_equal(
            w["range_high"], expected_rh, check_names=False)

    def test_wvf_uses_rolling_max_of_close(self):
        df = make_frame()
        w = williams_vix_fix(df, lookback_high=22, percentile_high=1.01)
        hh = df["close"].rolling(22).max()
        expected = 100.0 * (hh - df["low"]) / hh
        pd.testing.assert_series_equal(w["wvf"], expected, check_names=False)

    def test_stochastic_value_sanity(self):
        s = stochastic(make_frame())
        self.assertTrue(s["k"].dropna().between(0, 100).all())
        self.assertTrue(s["d"].dropna().between(0, 100).all())
        # raw %K (smooth_k=1) at the hammer: 100*(76-70)/(100.9-70)
        self.assertAlmostEqual(s["k"].iloc[80], 100 * 6.0 / 30.9, places=6)

    def test_stochastic_reaches_both_extremes(self):
        rows = [dict(open=100, high=110, low=90, close=100, volume=1.0)] * 15
        rows.append(dict(open=110, high=110, low=90, close=110, volume=1.0))
        rows.append(dict(open=90, high=110, low=90, close=90, volume=1.0))
        s = stochastic(pd.DataFrame(rows))
        self.assertAlmostEqual(s["k"].iloc[15], 100.0, places=6)
        self.assertAlmostEqual(s["k"].iloc[16], 0.0, places=6)


class SignalTests(unittest.TestCase):
    def _signals(self, df: pd.DataFrame, **params) -> list:
        opts = dict(SAFE)
        opts.update(params)
        strat = build("wvfx_stoch", **opts)
        prepared = strat.prepare(df)
        out = []
        for i in range(len(prepared)):
            sig = strat.signal(i, prepared, 0.0)
            if sig is not None:
                out.append((i, sig))
        return out

    def test_entry_on_first_green_bar_with_stoch_turn(self):
        sigs = self._signals(make_frame())
        self.assertTrue(sigs, "expected an entry signal")
        i, sig = sigs[0]
        self.assertEqual(i, 80)
        self.assertEqual(sig.action.value, "buy")
        close = float(make_frame()["close"].iloc[80])
        self.assertLess(sig.stop_loss, close)
        risk = close - sig.stop_loss
        self.assertAlmostEqual(sig.take_profit, close + 2.0 * risk, places=6)

    def test_green_event_only_on_first_green_bar(self):
        strat = build("wvfx_stoch", **SAFE)
        p = strat.prepare(make_frame())
        self.assertTrue(bool(p["green_event"].iloc[80]))
        self.assertFalse(bool(p["green_event"].iloc[81]))
        self.assertFalse(bool(p["green_event"].iloc[79]))

    def test_green_mode_any_matches_the_literal_rule(self):
        p = build("wvfx_stoch", **dict(SAFE, green_mode="any")).prepare(make_frame())
        self.assertTrue((p["green_flash"].astype(bool) == p["green"].astype(bool)).all())

    def test_green_mode_first_is_the_default(self):
        p = build("wvfx_stoch", **SAFE).prepare(make_frame())
        self.assertTrue(
            (p["green_flash"].astype(bool) == p["green_event"].astype(bool)).all())

    def test_green_mode_validation(self):
        with self.assertRaises(ValueError):
            build("wvfx_stoch", green_mode="every")

    def test_no_entry_without_crash(self):
        self.assertEqual(self._signals(flat_frame()), [])

    def test_oversold_gate_blocks_entry(self):
        # Hammer %K is 19.4; a 5% threshold can never be hit -> no entry.
        self.assertEqual(self._signals(make_frame(), oversold=5.0), [])

    def test_no_entry_when_k_below_d(self):
        strat = build("wvfx_stoch", **dict(SAFE, trigger="state"))
        p = strat.prepare(make_frame())
        for i in range(77, 100):
            p.iloc[i, p.columns.get_loc("k")] = 5.0
            p.iloc[i, p.columns.get_loc("d")] = 15.0
        out = []
        for i in range(len(p)):
            sig = strat.signal(i, p, 0.0)
            if sig is not None:
                out.append((i, sig))
        self.assertEqual(out, [])

    def test_state_and_cross_trigger_modes(self):
        # Already above %D on the previous bar: state passes, cross blocks.
        p = build("wvfx_stoch", **SAFE).prepare(make_frame())
        row = p.columns.get_loc
        i = 80
        p.iloc[i, row("green_flash")] = True
        p.iloc[i, row("oversold_flash")] = True
        p.iloc[i, row("k")] = 15.0
        p.iloc[i, row("d")] = 10.0
        p.iloc[i, row("atr")] = 1.0
        p.iloc[i, row("close")] = 100.0
        p.iloc[i, row("k_cross_up")] = False

        state = build("wvfx_stoch", **dict(SAFE, trigger="state"))
        state.prepare(make_frame())
        self.assertIsNotNone(state.signal(i, p, 0.0))

        cross = build("wvfx_stoch", **dict(SAFE, trigger="cross"))
        cross.prepare(make_frame())
        self.assertIsNone(cross.signal(i, p, 0.0))

    def test_k_cross_up_column(self):
        p = build("wvfx_stoch", **SAFE).prepare(make_frame())
        self.assertTrue(bool(p["k_cross_up"].iloc[80]))
        self.assertFalse(bool(p["k_cross_up"].iloc[79]))

    def test_regime_filter_blocks_entry(self):
        p = build("wvfx_stoch", **dict(SAFE, regime_days=5)).prepare(make_frame())
        self.assertTrue(bool(p["regime_bear"].iloc[80]))
        strat = build("wvfx_stoch", **dict(SAFE, regime_days=5, trigger="state"))
        p = strat.prepare(make_frame())
        self.assertIsNotNone(strat.signal(80, p, 0.0))
        p.iloc[80, p.columns.get_loc("regime_bear")] = False
        self.assertIsNone(strat.signal(80, p, 0.0))

    def test_exit_cross_closes_position(self):
        strat = build("wvfx_stoch", **dict(SAFE, exit_cross=True))
        p = strat.prepare(make_frame())
        self.assertIsNone(strat.signal(80, p, 1.0))  # remembers entry, no exit
        p.iloc[81, p.columns.get_loc("k")] = 10.0
        p.iloc[81, p.columns.get_loc("d")] = 40.0
        sig = strat.signal(81, p, 1.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action.value, "close")
        self.assertIn("crossed below", sig.reason)

    def test_time_exit(self):
        strat = build("wvfx_stoch", **dict(SAFE, max_hold=3))
        p = strat.prepare(make_frame())
        self.assertIsNone(strat.signal(80, p, 1.0))  # remembers entry, no exit
        sig = strat.signal(83, p, 1.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action.value, "close")
        self.assertIn("time exit", sig.reason)

    def test_rsi_flip_gate_blocks_entry(self):
        strat = build("wvfx_stoch", **dict(SAFE, require_rsi_flip=True))
        p = strat.prepare(make_frame())
        i, col = 80, p.columns.get_loc
        p.iloc[i, col("green_flash")] = True
        p.iloc[i, col("oversold_flash")] = True
        p.iloc[i, col("k_cross_up")] = True
        p.iloc[i, col("k")] = 15.0
        p.iloc[i, col("d")] = 10.0
        p.iloc[i, col("atr")] = 1.0
        p.iloc[i, col("close")] = 100.0
        p.iloc[i, col("rsi_flip_up_recent")] = False
        self.assertIsNone(strat.signal(i, p, 0.0))
        p.iloc[i, col("rsi_flip_up_recent")] = True
        self.assertIsNotNone(strat.signal(i, p, 0.0))

    def test_rsi_overbought_flip_exits(self):
        strat = build("wvfx_stoch", **dict(SAFE, exit_rsi_overbought=True))
        p = strat.prepare(make_frame())
        self.assertIsNone(strat.signal(80, p, 1.0))  # remembers entry
        p.iloc[81, p.columns.get_loc("rsi_flip_down")] = True
        sig = strat.signal(81, p, 1.0)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action.value, "close")
        self.assertIn("RSI", sig.reason)

    def test_stochastic_14_3_5_is_configurable(self):
        p = build("wvfx_stoch", **dict(SAFE, smooth_k=3, smooth_d=5)).prepare(make_frame())
        self.assertTrue(p["k"].dropna().between(0, 100).all())
        self.assertTrue(p["d"].dropna().between(0, 100).all())

    def test_param_validation(self):
        for bad in (dict(oversold_window=0), dict(entry_window=0),
                    dict(trigger="bad"), dict(regime_days=-1),
                    dict(oversold=200), dict(rsi_window=1),
                    dict(rsi_flip_window=0), dict(rsi_oversold=80,
                                                    rsi_overbought=70)):
            with self.assertRaises(ValueError):
                build("wvfx_stoch", **bad)
        with self.assertRaises(TypeError):
            build("wvfx_stoch", not_a_param=1)


class CausalityTests(unittest.TestCase):
    def test_prepared_columns_are_prefix_stable(self):
        df = make_frame(n=160, crash=120)
        strat = WilliamVixFixStoch(**SAFE)
        full = strat.prepare(df)
        cut = strat.prepare(df.iloc[:100].reset_index(drop=True))
        cols = ["wvf", "wvf_green", "k", "d", "atr", "rsi",
                "rsi_flip_up", "rsi_flip_down", "rsi_flip_up_recent",
                "green_event", "green_flash", "oversold_flash", "k_cross_up"]
        for c in cols:
            a = full[c].iloc[:100].reset_index(drop=True)
            b = cut[c]
            if str(a.dtype) == "boolean":
                self.assertTrue((a.astype("boolean").fillna(False)
                                 == b.astype("boolean").fillna(False)).all(), c)
            else:
                pd.testing.assert_series_equal(a, b, check_names=False)

    def test_no_green_event_before_the_slide(self):
        p = build("wvfx_stoch", **SAFE).prepare(make_frame())
        self.assertFalse(p["green_event"].iloc[:77].any())

    def test_engine_end_to_end_fills_at_next_open(self):
        """The one signal is at bar 80; the fill must be at the next open."""
        from bot.backtest import Engine
        from bot.config import BotConfig, CostConfig, RiskConfig

        df = make_frame()
        strat = build("wvfx_stoch", **SAFE)
        engine = Engine(
            df, strat,
            BotConfig(initial_cash=1000.0,
                      costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),
                      risk=RiskConfig(risk_per_trade=0.02, max_position_pct=0.95)),
        )
        engine.run()
        buys = [f for f in engine.broker.fills if f.side == "buy"]
        self.assertTrue(buys, "expected an entry fill")
        self.assertEqual(buys[0].ts, df["ts"].iloc[81])


if __name__ == "__main__":
    unittest.main()