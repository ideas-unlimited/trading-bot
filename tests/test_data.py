import unittest

from bot import indicators as ind
from bot.data.feed import Bar, _parse_ts, generate_synthetic, load_csv, write_bars

import pandas as pd
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TestIndicators(unittest.TestCase):
    def test_sma_matches_manual_average(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = ind.sma(s, 3)
        self.assertTrue(out.iloc[:2].isna().all())
        self.assertAlmostEqual(out.iloc[2], 2.0)
        self.assertAlmostEqual(out.iloc[4], 4.0)

    def test_ema_tracks_constant_series(self):
        s = pd.Series([7.0] * 40)
        out = ind.ema(s, 10).dropna()
        self.assertTrue((abs(out - 7.0) < 1e-9).all())

    def test_rsi_bounds_and_extremes(self):
        up = pd.Series([float(i) for i in range(1, 60)])
        self.assertAlmostEqual(ind.rsi(up, 14).iloc[-1], 100.0, places=6)
        down = pd.Series([float(i) for i in range(60, 1, -1)])
        self.assertAlmostEqual(ind.rsi(down, 14).iloc[-1], 0.0, places=6)
        flat = pd.Series([5.0] * 40)
        self.assertAlmostEqual(ind.rsi(flat, 14).iloc[-1], 100.0)

    def test_atr_positive_and_warmup(self):
        n = 60
        high = pd.Series([101.0 + i for i in range(n)])
        low = pd.Series([99.0 + i for i in range(n)])
        close = pd.Series([100.0 + i for i in range(n)])
        out = ind.atr(high, low, close, 14)
        self.assertTrue(out.iloc[:13].isna().all())
        self.assertGreater(out.iloc[-1], 0)


class TestFeed(unittest.TestCase):
    def test_bar_rejects_bad_ohlc(self):
        with self.assertRaises(ValueError):
            Bar(datetime.now(timezone.utc), 10, 9, 11, 10)

    def test_synthetic_is_deterministic(self):
        a = generate_synthetic(50, seed=3)
        b = generate_synthetic(50, seed=3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, generate_synthetic(50, seed=4))

    def test_synthetic_ohlc_consistency(self):
        for bar in generate_synthetic(300, seed=11):
            self.assertLessEqual(bar.low, min(bar.open, bar.close))
            self.assertGreaterEqual(bar.high, max(bar.open, bar.close))

    def test_csv_roundtrip(self):
        bars = generate_synthetic(20, seed=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_bars(Path(tmp) / "x.csv", bars)
            back = load_csv(path)
        self.assertEqual(len(back), len(bars))
        for orig, loaded in zip(bars, back):
            self.assertEqual(orig.ts, loaded.ts)
            self.assertAlmostEqual(orig.close, loaded.close, places=6)

    def test_csv_accepts_epoch_and_missing_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.csv"
            path.write_text(
                "timestamp,open,high,low,close\n1700000000,1,2,0.5,1.5\n", encoding="utf-8"
            )
            bars = load_csv(path)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0].close, 1.5)
        self.assertEqual(bars[0].ts.year, 2023)

    def test_missing_column_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("timestamp,open,close\n2024-01-01,1,2\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_csv(path)

    def test_parse_ts_millis(self):
        self.assertEqual(_parse_ts("1700000000000"), _parse_ts("1700000000"))


if __name__ == "__main__":
    unittest.main()
