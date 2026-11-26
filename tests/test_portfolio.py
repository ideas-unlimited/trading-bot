
"""Tests for the multi-coin trend rotation."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from bot.portfolio import RotationConfig, simulate_rotation, target_weights, summarize


def prices(n: int = 800, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    out = {}
    for k, name in enumerate(("BTC", "ETH", "SOL")):
        drift = 0.0012 - k * 0.0004
        shocks = rng.normal(0, 0.02, n)
        out[name] = 100.0 * np.exp(np.cumsum(drift + shocks))
    return pd.DataFrame(out, index=idx)


class RotationTests(unittest.TestCase):
    def test_weights_respect_top_n_and_cash(self) -> None:
        closes = prices()
        cfg = RotationConfig(lookback=60, top_n=2)
        w = target_weights(closes, cfg)
        self.assertTrue((w >= 0).all().all())
        self.assertLessEqual(float(w.sum(axis=1).max()), 1.0 + 1e-9)
        self.assertLessEqual(int((w > 0).sum(axis=1).max()), 2)

    def test_no_lookahead_in_held_weights(self) -> None:
        closes = prices()
        cfg = RotationConfig(lookback=60, top_n=2, rebalance_days=7)
        res = simulate_rotation(closes, cfg)
        # The first bar can never hold a position: weights are shifted.
        self.assertEqual(float(res.weights.iloc[0].sum()), 0.0)
        # Changing tomorrow's price must not change today's holding.
        tampered = closes.copy()
        tampered.iloc[-1] *= 3.0
        res2 = simulate_rotation(tampered, cfg)
        pd.testing.assert_frame_equal(res.weights, res2.weights)

    def test_equity_starts_at_initial_cash_and_length_matches(self) -> None:
        closes = prices()
        res = simulate_rotation(closes, RotationConfig(initial_cash=1_000.0))
        self.assertEqual(len(res.equity), len(closes))
        self.assertAlmostEqual(float(res.equity.iloc[0]), 1_000.0, places=6)
        self.assertGreater(float(res.equity.iloc[-1]), 0.0)

    def test_fees_only_reduce_equity(self) -> None:
        closes = prices()
        free = simulate_rotation(closes, RotationConfig(fee_bps=0.0))
        costly = simulate_rotation(closes, RotationConfig(fee_bps=50.0))
        self.assertLessEqual(float(costly.equity.iloc[-1]), float(free.equity.iloc[-1]) + 1e-9)

    def test_flat_market_stays_in_cash(self) -> None:
        idx = pd.date_range("2023-01-01", periods=400, freq="D")
        closes = pd.DataFrame({c: np.full(400, 100.0) for c in ("BTC", "ETH")}, index=idx)
        res = simulate_rotation(closes, RotationConfig())
        self.assertAlmostEqual(float(res.equity.iloc[-1]), 1_000.0, places=6)
        self.assertEqual(float(res.weights.sum(axis=1).max()), 0.0)

    def test_summary_is_sane(self) -> None:
        res = simulate_rotation(prices(), RotationConfig())
        s = summarize(res)
        for key in ("cagr", "sharpe", "max_drawdown", "exposure", "final"):
            self.assertIn(key, s)
        self.assertLessEqual(s["max_drawdown"], 0.0)
        self.assertGreaterEqual(s["exposure"], 0.0)
        self.assertLessEqual(s["exposure"], 1.0)


if __name__ == "__main__":
    unittest.main()
