"""Offline tests for the TradingView data adapter (no network)."""

import unittest
from datetime import datetime, timezone

import pandas as pd

from bot.data.tvfeed import frame_to_bars, interval_attr


def _sample_df() -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3)],
        name="datetime",
    )
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, float("nan")],
            "high": [102.0, 103.0, float("nan")],
            "low": [99.0, 100.0, float("nan")],
            "close": [101.0, 102.5, float("nan")],
            "volume": [1000.0, 1200.0, float("nan")],
        },
        index=idx,
    )


class FrameToBarsTests(unittest.TestCase):
    def test_converts_and_drops_incomplete_rows(self) -> None:
        bars = frame_to_bars(_sample_df())
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].ts, datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[1].close, 102.5)
        self.assertEqual(bars[1].volume, 1200.0)

    def test_naive_timestamps_become_utc(self) -> None:
        bars = frame_to_bars(_sample_df())
        self.assertIsNotNone(bars[0].ts.tzinfo)

    def test_empty_and_none(self) -> None:
        self.assertEqual(frame_to_bars(None), [])
        self.assertEqual(frame_to_bars(pd.DataFrame()), [])


class IntervalTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(interval_attr("1h"), "in_1_hour")
        self.assertEqual(interval_attr("60m"), "in_1_hour")
        self.assertEqual(interval_attr("1d"), "in_daily")
        self.assertEqual(interval_attr("1M"), "in_monthly")

    def test_unknown_interval_raises(self) -> None:
        with self.assertRaises(ValueError):
            interval_attr("7h")


if __name__ == "__main__":
    unittest.main()
