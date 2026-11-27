"""Binance order-book depth: parsing, features, fetching and recording."""

import unittest
from datetime import datetime, timezone

from bot.data import depth


def payload(bids, asks, update=42):
    return {"lastUpdateId": update, "bids": bids, "asks": asks}


GOOD = payload([["100.0", "2.0"], ["99.5", "3.0"]], [["100.5", "1.5"], ["101.0", "4.0"]])


class SymbolTests(unittest.TestCase):
    def test_normalises(self):
        self.assertEqual(depth.normalize_symbol("btc/usdt"), "BTCUSDT")
        self.assertEqual(depth.normalize_symbol("ETH-USDT"), "ETHUSDT")

    def test_rejects_junk(self):
        for bad in ("", "  ", "BTCUSDT;drop", "BTC USDT", "Ⅳ"):
            with self.assertRaises(ValueError):
                depth.normalize_symbol(bad)


class ParseTests(unittest.TestCase):
    def test_parses_and_orders_levels(self):
        ts = datetime(2026, 11, 27, tzinfo=timezone.utc)
        snap = depth.parse_depth(GOOD, "btcusdt", ts=ts)
        self.assertEqual(snap.symbol, "BTCUSDT")
        self.assertEqual(snap.ts, ts)
        self.assertEqual(snap.last_update_id, 42)
        self.assertEqual([l.price for l in snap.bids], [100.0, 99.5])
        self.assertEqual([l.price for l in snap.asks], [100.5, 101.0])

    def test_rejects_crossed_book(self):
        bad = payload([["101.0", "1"]], [["100.0", "1"]])
        with self.assertRaises(depth.DepthError):
            depth.parse_depth(bad, "BTCUSDT")

    def test_rejects_unsorted(self):
        bad = payload([["99.0", "1"], ["100.0", "1"]], [["101.0", "1"]])
        with self.assertRaises(depth.DepthError):
            depth.parse_depth(bad, "BTCUSDT")

    def test_rejects_non_positive_level(self):
        bad = payload([["0", "1"]], [["101.0", "1"]])
        with self.assertRaises(depth.DepthError):
            depth.parse_depth(bad, "BTCUSDT")

    def test_rejects_missing_arrays(self):
        with self.assertRaises(depth.DepthError):
            depth.parse_depth({"bids": []}, "BTCUSDT")

    def test_rejects_empty_book(self):
        with self.assertRaises(depth.DepthError):
            depth.parse_depth(payload([], []), "BTCUSDT")


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.snap = depth.parse_depth(GOOD, "BTCUSDT")

    def test_mid_and_spread(self):
        self.assertAlmostEqual(self.snap.mid, 100.25)
        expected = (100.5 - 100.0) / 100.25 * 10_000
        self.assertAlmostEqual(self.snap.spread_bps, expected)

    def test_imbalance_sign(self):
        self.assertLess(self.snap.imbalance(2), 0)  # more ask qty than bid
        self.assertAlmostEqual(self.snap.imbalance(2), (5.0 - 5.5) / 10.5)

    def test_notional_within_bps(self):
        self.assertAlmostEqual(self.snap.notional("bid", bps=100), 100.0 * 2.0 + 99.5 * 3.0)
        self.assertAlmostEqual(self.snap.notional("ask", bps=100), 100.5 * 1.5 + 101.0 * 4.0)
        self.assertEqual(self.snap.notional("bid", bps=0), 0.0)


class FetchTests(unittest.TestCase):
    def test_builds_production_url(self):
        seen = {}

        def transport(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return GOOD

        depth.fetch_depth("btc/usdt", limit=5, transport=transport, timeout=3.0)
        self.assertTrue(seen["url"].startswith("https://api.binance.com/api/v3/depth?"))
        self.assertIn("symbol=BTCUSDT", seen["url"])
        self.assertIn("limit=5", seen["url"])
        self.assertEqual(seen["timeout"], 3.0)

    def test_rejects_bad_limit_before_network(self):
        def transport(url, timeout):
            raise AssertionError("must not call the network")

        for bad in (0, -1, 5001, True):
            with self.assertRaises(ValueError):
                depth.fetch_depth("BTCUSDT", limit=bad, transport=transport)

    def test_book_ticker_parses(self):
        def transport(url, timeout):
            self.assertIn("/api/v3/ticker/bookTicker", url)
            return {"bidPrice": "100.0", "askPrice": "100.5", "bidQty": "2", "askQty": "3"}

        quote = depth.fetch_book_ticker("BTCUSDT", transport=transport)
        self.assertEqual(quote["bid"], 100.0)
        self.assertEqual(quote["ask"], 100.5)

    def test_agg_trades_parse(self):
        def transport(url, timeout):
            return [{"a": 7, "p": "100.0", "q": "1.5", "T": 1700000000000, "m": True}]

        trades = depth.fetch_agg_trades("BTCUSDT", transport=transport)
        self.assertEqual(trades[0]["price"], 100.0)
        self.assertTrue(trades[0]["maker"])
        self.assertEqual(trades[0]["ts"].year, 2023)


class RecorderTests(unittest.TestCase):
    def test_record_once_persists_each_snapshot(self):
        calls = []

        class Conn:
            def execute(self, sql, args=None):
                calls.append((sql, args))

        def transport(url, timeout):
            return GOOD

        snaps = depth.record_once(["BTCUSDT", "ETHUSDT"], limit=2, conn=Conn(),
                                  transport=transport)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("INSERT INTO book_snapshots", calls[0][0])
        self.assertEqual(calls[0][1][0], "BTCUSDT")

    def test_recorder_stops_and_sleeps(self):
        ticks = {"n": 0}

        def stop():
            ticks["n"] += 1
            return ticks["n"] > 2

        slept = []
        cycles = depth.run_recorder(["BTCUSDT"], every=60, limit=2,
                                    transport=lambda url, timeout: GOOD,
                                    stop=stop, sleep=slept.append)
        self.assertGreaterEqual(cycles, 1)
        self.assertTrue(all(s == 60 for s in slept))

    def test_recorder_rejects_zero_interval(self):
        with self.assertRaises(ValueError):
            depth.run_recorder(["BTCUSDT"], every=0, transport=lambda u, t: GOOD)


if __name__ == "__main__":
    unittest.main()
