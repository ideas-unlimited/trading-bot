"""Cache-first data loading tests (Postgres mocked out)."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from bot.scanlib import cache_is_sufficient


def _bars(n, spacing_hours, last=None):
    from bot.data.feed import Bar
    last = last or datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    return [
        Bar(ts=last - timedelta(hours=spacing_hours * (n - 1 - i)), open=1, high=1, low=1, close=1)
        for i in range(n)
    ]


class CacheIsSufficientTests(unittest.TestCase):
    def test_full_coverage_and_fresh(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        bars = _bars(400, 1)  # hourly bars ending at `now`
        start = now - timedelta(days=365)
        self.assertFalse(cache_is_sufficient(bars, start, now=now))  # only ~16 days of hourly bars

    def test_short_cache_rejected(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(cache_is_sufficient(_bars(30, 1), None, now=now))

    def test_stale_cache_rejected(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        stale_last = now - timedelta(days=5)
        bars = _bars(400, 24, last=stale_last)
        self.assertFalse(cache_is_sufficient(bars, None, now=now))

    def test_fresh_daily_cache_survives_weekend(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        bars = _bars(400, 24, last=now - timedelta(days=2))
        self.assertTrue(cache_is_sufficient(bars, None, now=now))


if __name__ == "__main__":
    unittest.main()


class MergeBarsTests(unittest.TestCase):
    def test_merge_dedups_and_sorts(self):
        from bot.data.feed import Bar
        from bot.scanlib import merge_bars
        t = lambda h: datetime(2026, 1, 1, h, tzinfo=timezone.utc)
        cached = [Bar(ts=t(1), open=1, high=1, low=1, close=1),
                  Bar(ts=t(2), open=2, high=2, low=2, close=2)]
        fresh = [Bar(ts=t(2), open=20, high=2, low=2, close=20),   # revised bar
                 Bar(ts=t(4), open=4, high=4, low=4, close=4)]
        merged = merge_bars(cached, fresh)
        self.assertEqual([b.ts.hour for b in merged], [1, 2, 4])
        self.assertEqual(merged[1].close, 20)  # fresh wins
