"""Tests for the volume-climax reversal strategy."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.strategy import build
from bot.strategy.volume_climax import VolumeClimax


def make_frame(n: int = 120, spike: int = 60, spike_volume: float = 2000.0,
               close_at: float | None = None) -> pd.DataFrame:
    """Steadily falling market with one volume spike at ``spike``."""
    rows = []
    for i in range(n):
        close = 100.0 - 0.2 * i
        hi, lo = close + 1.0, close - 1.0
        vol = 100.0
        if i == spike:
            vol = spike_volume
            hi, lo = close + 1.5, close - 3.0
            if close_at is not None:
                close = close_at
        rows.append(dict(open=close, high=hi, low=lo, close=close, volume=vol))
    return pd.DataFrame(rows)


class SignalTests(unittest.TestCase):
    def _signals(self, df: pd.DataFrame, **params) -> list:
        strat = build("vol_climax", **params)
        prepared = strat.prepare(df)
        out = []
        for i in range(len(prepared)):
            sig = strat.signal(i, prepared, 0.0)
            if sig is not None:
                out.append((i, sig))
        return out

    def test_climax_at_a_discount_fires(self):
        # Spike bar closes near the top of its range -> absorption.
        df = make_frame(spike_volume=2000.0)
        avg_close = float(df["close"].iloc[60])
        sigs = self._signals(df, drop_pct=0.05)
        self.assertEqual([i for i, _ in sigs], [60])
        signal = sigs[0][1]
        self.assertEqual(signal.action.value, "buy")
        self.assertLess(signal.stop_loss, float(df["low"].iloc[60]))
        risk = avg_close - signal.stop_loss
        self.assertAlmostEqual(signal.take_profit, avg_close + 2.0 * risk, places=6)

    def test_no_signal_without_volume_spike(self):
        df = make_frame(spike_volume=100.0)
        self.assertEqual(self._signals(df, drop_pct=0.05), [])

    def test_no_signal_when_bar_closes_at_the_lows(self):
        # Same volume spike, but the bar closes in the lower quartile:
        # sellers are still in control, so there is no absorption to buy.
        df = make_frame(spike_volume=2000.0, close_at=85.0)
        self.assertEqual(self._signals(df, drop_pct=0.05), [])

    def test_no_signal_without_a_decline(self):
        df = make_frame(spike_volume=2000.0)
        df["close"] = 100.0  # flat, never in a drawdown
        df["high"] = 101.0
        df["low"] = 99.0
        df["open"] = 100.0
        self.assertEqual(self._signals(df, drop_pct=0.05), [])

    def test_volume_zscore_ignores_the_bar_itself(self):
        df = make_frame(spike_volume=100.0)  # no spike: baseline z-scores
        strat = VolumeClimax()
        prepared = strat.prepare(df)
        self.assertTrue(prepared["vol_z"].iloc[25:].abs().max() < 1e-9)


class CausalityTests(unittest.TestCase):
    def test_prepared_columns_are_prefix_stable(self):
        df = make_frame(n=160, spike=120, spike_volume=3000.0)
        full = VolumeClimax().prepare(df)
        part = VolumeClimax().prepare(df.iloc[:130])
        for col in ("vol_z", "prior_high", "drawdown", "close_pos", "atr"):
            a = full[col].iloc[25:130].to_numpy(dtype=float)
            b = part[col].iloc[25:130].to_numpy(dtype=float)
            for x, y in zip(a, b):
                if pd.isna(x) or pd.isna(y):
                    self.assertTrue(pd.isna(x) and pd.isna(y), f"{col}: NaN mismatch")
                else:
                    self.assertAlmostEqual(x, y, places=9, msg=f"{col} changed")


class EngineTests(unittest.TestCase):
    def test_engine_fills_the_next_open(self):
        from datetime import datetime, timedelta, timezone

        from bot.backtest import Engine, bars_to_frame
        from bot.config import BotConfig, CostConfig
        from bot.data.feed import Bar

        df = make_frame(n=80, spike=40, spike_volume=5000.0)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            Bar(
                ts=start + timedelta(hours=i),
                open=float(r.open), high=float(r.high), low=float(r.low),
                close=float(r.close), volume=float(r.volume),
            )
            for i, r in enumerate(df.itertuples())
        ]
        cfg = BotConfig(costs=CostConfig(fee_bps=0.0, slippage_bps=0.0))
        engine = Engine(bars_to_frame(bars), build("vol_climax", drop_pct=0.05), cfg)
        engine.run()
        result = engine.result()
        self.assertGreaterEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertAlmostEqual(trade.entry_price, bars[41].open, places=6)


if __name__ == "__main__":
    unittest.main()
