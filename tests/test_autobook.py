'''Live paper books: one replay per wallet, no orders, no lookahead.'''

from __future__ import annotations

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from bot import autobook
from bot.autobook import BookSpec, closed_bars, run_once, select_books
from bot.book import BookConfig, simulate_book
from bot.data import binance
from bot.data.feed import Bar


def make_bars(closes, *, tf_hours=4, start=None, spread=0.5):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(start + timedelta(hours=tf_hours * i), c, c + spread, c - spread, c, 1.0)
        for i, c in enumerate(closes)
    ]


def walk(seed, n=220, drift=0.0025, vol=0.02):
    '''Seeded random walk with drift: crosses, stop-outs and filter states.'''
    rnd = random.Random(seed)
    px = 100.0
    out = []
    for _ in range(n):
        px *= 1.0 + drift + rnd.gauss(0.0, vol)
        out.append(round(px, 4))
    return out


class SelectBooksTests(unittest.TestCase):
    def test_comma_list_and_cash_override(self):
        books = select_books('A,C', cash=250.0)
        self.assertEqual([b.key for b in books], ['A', 'C'])
        self.assertEqual([b.cash for b in books], [250.0, 250.0])

    def test_all_keyword(self):
        self.assertEqual([b.key for b in select_books('all')], ['A', 'B', 'C', 'D', 'E'])

    def test_unknown_book_raises(self):
        with self.assertRaises(ValueError):
            select_books('A,Z')

    def test_each_book_has_a_thousand(self):
        for spec in autobook.default_books():
            self.assertEqual(spec.cash, 1_000.0)


class ClosedBarTests(unittest.TestCase):
    def test_forming_bar_is_dropped(self):
        now = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
        bars = make_bars([100.0, 101.0, 102.0], start=datetime(2024, 1, 1, tzinfo=timezone.utc))
        # bars at 00:00, 04:00, 08:00 -> the 04:00 bar closes exactly at 08:00,
        # the 08:00 bar is still forming at 06:00? no: at 06:00 only 00:00 closed.
        kept = closed_bars(bars, '4h', now)
        self.assertEqual([b.ts.hour for b in kept], [0])

    def test_closed_bar_kept_at_boundary(self):
        now = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
        bars = make_bars([100.0, 101.0], start=datetime(2024, 1, 1, tzinfo=timezone.utc))
        kept = closed_bars(bars, '4h', now)
        self.assertEqual([b.ts.hour for b in kept], [0, 4])


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.bars = {'AAA': make_bars(walk(3)), 'BBB': make_bars(walk(5))}
        self.now = self.bars['AAA'][-1].ts + timedelta(hours=5)

    def _run(self, outdir, **kwargs):
        return run_once(['AAA', 'BBB'], books=autobook.default_books(), outdir=outdir,
                        bars_by_symbol=self.bars, now=self.now, **kwargs)

    def test_wallets_and_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run(Path(tmp))
            keys = [b.key for b in autobook.default_books()]
            self.assertEqual(sorted(payload['books']), keys)
            status = json.loads((Path(tmp) / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['books']['A']['deploy_equity'],
                             payload['books']['A']['equity'])
            self.assertEqual(payload['books']['A']['since_deploy'], 0.0)
            for key in keys:
                self.assertTrue((Path(tmp) / key / 'trades.csv').exists())
                self.assertTrue((Path(tmp) / key / 'equity.csv').exists())

    def test_risk_scales_the_same_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run(Path(tmp))
            a, b = payload['books']['A'], payload['books']['B']
            self.assertEqual(a['trades'], b['trades'])
            self.assertGreater(b['equity'], a['equity'])

    def test_plain_book_trades_at_least_as_often(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run(Path(tmp))
            self.assertGreaterEqual(payload['books']['C']['trades'],
                                    payload['books']['A']['trades'])

    def test_second_run_measures_since_deploy(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._run(Path(tmp))
            second = self._run(Path(tmp))
            self.assertEqual(first['books']['A']['deploy_equity'],
                             second['books']['A']['deploy_equity'])
            self.assertAlmostEqual(second['books']['A']['since_deploy'], 0.0, places=12)

    def test_since_deploy_tracks_equity(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._run(Path(tmp))
            state_path = Path(tmp) / 'status.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['books']['A']['deploy_equity'] = first['books']['A']['equity'] / 2.0
            state_path.write_text(json.dumps(state), encoding='utf-8')
            again = self._run(Path(tmp))
            self.assertAlmostEqual(again['books']['A']['since_deploy'], 1.0, places=9)

    def test_window_shift_repins_the_deploy_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._run(Path(tmp))
            path = Path(tmp) / 'status.json'
            state = json.loads(path.read_text(encoding='utf-8'))
            state['books']['A']['window_start'] = '2020-01-01T00:00:00+00:00'
            path.write_text(json.dumps(state), encoding='utf-8')
            again = self._run(Path(tmp))
            self.assertEqual(again['books']['A']['since_deploy'], 0.0)
            self.assertEqual(again['books']['A']['repinned_at'],
                             first['books']['A']['deployed_at'])

    def test_auto_default_runs_every_book(self):
        from bot.cli import build_parser
        self.assertEqual(build_parser().parse_args(['auto']).books, 'all')

    def test_no_closed_bars_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                run_once(['AAA'], outdir=Path(tmp), bars_by_symbol={'AAA': make_bars([100.0])},
                         books=autobook.default_books(), now=self.now)


class OpenPositionTests(unittest.TestCase):
    def test_live_book_keeps_the_open_position(self):
        closes = [100.0] * 20 + [100.0 + 2.0 * i for i in range(1, 40)]
        bars = make_bars(closes)
        cfg = BookConfig(fast=2, slow=3, fee_bps=0.0, slippage_bps=0.0, atr_mult=5.0)
        closed = simulate_book({'A': bars}, cfg, close_at_end=True)
        live = simulate_book({'A': bars}, cfg, close_at_end=False)
        self.assertEqual(closed.positions, {})
        self.assertIn('A', live.positions)
        self.assertEqual(live.positions['A']['qty'], live.positions['A']['qty'])
        self.assertAlmostEqual(live.final_equity, closed.final_equity,
                               delta=covered_delta(live.final_equity))


def covered_delta(value):
    '''Two fills at 5 bps are all that may differ between the two endings.'''
    return abs(value) * 0.01 + 1.0


class BinanceTests(unittest.TestCase):
    def test_parse_kline(self):
        row = [1704067200000, '100.0', '101.0', '99.0', '100.5', '12.0',
               1704081599999, '1200.0', 10, '6.0', '600.0', '0']
        bar = binance.parse_kline(row)
        self.assertEqual(bar.ts, datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (100.0, 101.0, 99.0, 100.5))
        self.assertEqual(bar.volume, 12.0)

    def test_pagination_follows_the_cursor(self):
        page1 = [[1704067200000 + i * 3_600_000, '1', '2', '0.5', '1.5', '1',
                  1704070799999 + i * 3_600_000, '0', 1, '0', '0', '0'] for i in range(3)]
        page2 = [[1704078000000, '1', '2', '0.5', '1.5', '1', 1704081599999,
                  '0', 1, '0', '0', '0']]
        calls = []

        def fake_get(url, timeout=20.0):
            calls.append(url)
            return page1 if len(calls) == 1 else page2

        with mock.patch.object(binance, '_get', fake_get):
            bars = binance.fetch_binance_bars('BTCUSDT', '1h',
                                              start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                                              limit=3)
        self.assertEqual(len(calls), 2)
        self.assertIn('startTime=', calls[0])
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars, sorted(bars, key=lambda b: b.ts))

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            binance.fetch_binance_bars('BTCUSDT', '7h')

    def test_symbol_slashes_removed(self):
        seen = {}

        def fake_get(url, timeout=20.0):
            seen['url'] = url
            return []

        with mock.patch.object(binance, '_get', fake_get):
            self.assertEqual(binance.fetch_binance_bars('BTC/USD', '4h'), [])
        self.assertIn('symbol=BTCUSD', seen['url'])


if __name__ == '__main__':
    unittest.main()