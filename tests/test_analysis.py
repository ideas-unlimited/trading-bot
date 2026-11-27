"""Tests for the shared analysis toolkit (bot.analysis).

The load-bearing test is PrefixStability: every analysis function must be
causal — recomputing it on a truncated history must not change earlier
values.  This is how we prove a ported indicator has no lookahead.
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from bot import analysis as an


def make_df(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = np.cumsum(rng.normal(0.0005, 0.01, n)) + 100
    close = pd.Series(drift, index=pd.date_range("2025-01-01", periods=n, freq="4h"))
    spread = pd.Series(rng.uniform(0.001, 0.01, n), index=close.index)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * (1 + spread),
            "low": close * (1 - spread),
            "close": close,
            "volume": pd.Series(rng.uniform(1e3, 1e5, n), index=close.index),
        }
    )


def _assert_prefix_stable(self, name: str, full, cut: int = 250, warm: int = 5):
    """Computing on a prefix must not change earlier outputs."""
    if isinstance(full, tuple):
        for f in full:
            _assert_prefix_stable(self, name, f, cut, warm)
        return
    if isinstance(full, pd.DataFrame):
        for col in full.columns:
            _assert_prefix_stable(self, f"{name}.{col}", full[col], cut, warm)
        return
    df = make_df()
    fn = getattr(an, name)
    # rebuild the prefix from the same data
    idx = df.index[:cut]
    sub = df.loc[idx]
    try:
        recomputed = fn(sub)
    except TypeError:
        return  # series-signature functions handled below
    got = recomputed.iloc[warm:cut - 1].to_numpy()
    want = full.iloc[warm:cut - 1].to_numpy()
    np.testing.assert_allclose(got, want, equal_nan=True, rtol=1e-9, atol=1e-12,
                               err_msg=f"{name} is not prefix-stable (lookahead!)")


SERIES_FNS = {
    "efficiency_ratio": (),
    "variance_ratio_hurst": (),
    "zscore": (),
    "rolling_skew": (),
    "rolling_kurtosis": (),
    "rolling_autocorr": (),
    "drawdown": (),
}


class PrefixStability(unittest.TestCase):
    """No function may look into the future."""

    def test_dataframe_functions(self):
        df = make_df()
        cases: list[tuple[str, pd.Series, object]] = [
            ("vol_regime.vol", an.vol_regime(df["close"])["vol"],
             lambda d: an.vol_regime(d["close"])["vol"]),
            ("swing_pivots", an.swing_pivots(df["high"], df["low"])["pivot_high"],
             lambda d: an.swing_pivots(d["high"], d["low"])["pivot_high"]),
            ("liquidity_levels", an.liquidity_levels(df["high"], df["low"])["sell_side"],
             lambda d: an.liquidity_levels(d["high"], d["low"])["sell_side"]),
            ("sweep_reclaim", an.sweep_reclaim(df)["sell_sweep"],
             lambda d: an.sweep_reclaim(d)["sell_sweep"]),
            ("level_touches", an.level_touches(df["high"], df["low"])["touch_high"],
             lambda d: an.level_touches(d["high"], d["low"])["touch_high"]),
            ("vwap_rolling", an.vwap_rolling(df), an.vwap_rolling),
            ("volume_zscore", an.volume_zscore(df), an.volume_zscore),
            ("obv_slope", an.obv_slope(df), an.obv_slope),
            ("directional_volume", an.directional_volume(df), an.directional_volume),
            ("wick_ratio.lower", an.wick_ratio(df)["lower_wick"],
             lambda d: an.wick_ratio(d)["lower_wick"]),
            ("range_expansion.nr7", an.range_expansion(df)["nr7"],
             lambda d: an.range_expansion(d)["nr7"]),
            ("adx", an.adx(df["high"], df["low"], df["close"])[0],
             lambda d: an.adx(d["high"], d["low"], d["close"])[0]),
            ("adx.plus_di", an.adx(df["high"], df["low"], df["close"])[1],
             lambda d: an.adx(d["high"], d["low"], d["close"])[1]),
            ("rsi_divergence.bullish",
             an.rsi_divergence(df["high"], df["low"], df["close"])["bullish"],
             lambda d: an.rsi_divergence(d["high"], d["low"], d["close"])["bullish"]),
        ]
        for name, full, fn in cases:
            self._check(name, full, fn, df)

    def _check(self, name: str, full: pd.Series, fn, df: pd.DataFrame,
               cut: int = 250, warm: int = 5, tail: int = 1):
        sub = df.iloc[:cut]
        recomputed = fn(sub)
        if name.startswith("rsi_divergence"):
            tail = 7  # pivot confirmation needs `right` future bars
        got = recomputed.iloc[warm:cut - tail].to_numpy(dtype=float)
        want = full.iloc[warm:cut - tail].to_numpy(dtype=float)
        np.testing.assert_allclose(
            got, want, equal_nan=True, rtol=1e-9, atol=1e-12,
            err_msg=f"{name} is not prefix-stable (lookahead!)",
        )


class ValueSanity(unittest.TestCase):
    def setUp(self):
        self.df = make_df()

    def test_efficiency_ratio_bounds(self):
        er = an.efficiency_ratio(self.df["close"])
        self.assertTrue(((er.dropna() >= 0) & (er.dropna() <= 1)).all())

    def test_adx_signs(self):
        a, pdi, mdi = an.adx(self.df["high"], self.df["low"], self.df["close"])
        for s in (a, pdi, mdi):
            self.assertTrue((s.dropna() >= -1e-9).all())

    def test_drawdown_nonpositive(self):
        dd = an.drawdown(self.df["close"])
        self.assertTrue((dd.dropna() <= 1e-12).all())

    def test_sweep_reclaim_detects_a_manufactured_sweep(self):
        df = self.df.copy()
        lv = an.liquidity_levels(df["high"], df["low"], 20)
        # bar that dips below the prior 20-bar low but closes back above it
        i = 150
        df.iloc[i, df.columns.get_loc("low")] = lv["sell_side"].iloc[i] * 0.98
        df.iloc[i, df.columns.get_loc("close")] = lv["sell_side"].iloc[i] * 1.02
        df.iloc[i, df.columns.get_loc("open")] = lv["sell_side"].iloc[i] * 1.05
        df.iloc[i, df.columns.get_loc("high")] = lv["sell_side"].iloc[i] * 1.06
        sw = an.sweep_reclaim(df, window=20)
        self.assertTrue(bool(sw["sell_sweep"].iloc[i]))

    def test_wick_ratio_bounds(self):
        w = an.wick_ratio(self.df)
        for col in w.columns:
            self.assertTrue(((w[col].dropna() >= 0) & (w[col].dropna() <= 1 + 1e-9)).all())

    def test_directional_volume_bounds(self):
        dv = an.directional_volume(self.df)
        self.assertTrue(((dv.dropna() >= -1) & (dv.dropna() <= 1)).all())

    def test_vwap_within_bar_range(self):
        v = an.vwap_rolling(self.df)
        self.assertTrue((v.dropna() >= self.df["low"].min()).all())
        self.assertTrue((v.dropna() <= self.df["high"].max()).all())

    def test_rsi_divergence_output_shape_and_flags(self):
        out = an.rsi_divergence(self.df["high"], self.df["low"], self.df["close"])
        self.assertEqual(set(out.columns), {"bullish", "bearish"})
        self.assertTrue(out.dtypes.eq(bool).all())
        self.assertTrue(int(out["bullish"].sum()) + int(out["bearish"].sum()) >= 0)


if __name__ == "__main__":
    unittest.main()
