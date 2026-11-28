"""Shared scan logic: database outage fallback."""

import unittest
from unittest import mock

from bot import scanlib


class HistoryCapTests(unittest.TestCase):
    def test_cap_is_two_years(self) -> None:
        self.assertEqual(scanlib.MAX_HISTORY_DAYS, 730)


class DatabaseOutageTests(unittest.TestCase):
    def test_falls_back_to_the_source_when_the_database_is_missing(self):
        from datetime import datetime, timezone

        class Bar:
            def __init__(self, ts):
                self.ts = ts

        sentinel = [Bar(datetime.now(timezone.utc))]
        with mock.patch.object(scanlib.store, "is_available", return_value=True), \
             mock.patch.object(scanlib.store, "connect_common",
                               side_effect=scanlib.store.DatabaseError("no db")), \
             mock.patch.object(scanlib, "_fetch_source", return_value=sentinel) as fetch:
            bars = scanlib.load_scan_bars("BTC/USDT", "4h", source="binance", days=30)
        self.assertEqual(bars, sentinel)
        self.assertEqual(fetch.call_args.kwargs["start"], mock.ANY)

    def test_fallback_slices_to_the_requested_window(self):
        class Bar:
            def __init__(self, ts):
                self.ts = ts

        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old = Bar(now - timedelta(days=400))
        new = Bar(now - timedelta(days=1))
        with mock.patch.object(scanlib.store, "is_available", return_value=True), \
             mock.patch.object(scanlib.store, "connect_common",
                               side_effect=scanlib.store.DatabaseError("no db")), \
             mock.patch.object(scanlib, "_fetch_source", return_value=[old, new]):
            bars = scanlib.load_scan_bars("BTC/USDT", "4h", source="binance", days=30)
        self.assertEqual(bars, [new])


if __name__ == "__main__":
    unittest.main()
