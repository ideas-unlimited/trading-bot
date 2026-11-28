"""Tests for the per-symbol forward runner (simulated replay, no orders)."""

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.data.feed import Bar
from bot.forward import run_cycle
from bot.strategy.base import Action, Signal, Strategy


def make_bars(n=12, overrides=None):
    """Flat 100 bars at 4h; overrides maps index -> (open, high, low, close)."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        o = h = l = c = 100.0
        if overrides and i in overrides:
            o, h, l, c = overrides[i]
        out.append(Bar(start + timedelta(hours=4 * i), o, h, l, c, 1000.0))
    return out


class BuyThenLeave(Strategy):
    name = 'test_buy_leave'
    warmup = 1
    PARAMS = ()

    def prepare(self, df):
        return df

    def signal(self, i, df, position):
        if position == 0 and i == 2:
            return Signal(Action.BUY, 'test entry', stop_loss=90.0, take_profit=110.0)
        return None


class BuyThenTimeExit(Strategy):
    name = 'test_buy_time'
    warmup = 1
    PARAMS = ()

    def prepare(self, df):
        return df

    def signal(self, i, df, position):
        if position == 0 and i == 2:
            return Signal(Action.BUY, 'test entry', stop_loss=90.0, take_profit=200.0)
        if position > 0 and i == 5:
            return Signal(Action.CLOSE, 'time test')
        return None


class TestForwardRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, strategy, bars, outdir=None, now=None):
        return run_cycle(
            strategy, 'TEST/USDT', '4h', params={}, capital=1_000.0,
            fee_bps=0.0, slippage_bps=0.0, risk_per_trade=0.05,
            max_position_pct=0.95, max_drawdown_stop=0.9,
            outdir=outdir or self.outdir, bars=bars,
            now=now or datetime(2024, 12, 1, tzinfo=timezone.utc),
        )

    def test_target_exit_is_honoured(self):
        bars = make_bars(overrides={4: (100.0, 112.0, 99.0, 108.0)})
        payload = self.run_with(BuyThenLeave(), bars)
        self.assertIsNone(payload['position'])
        self.assertEqual(payload['history']['trades'], 1)
        with (self.outdir / 'test_buy_leave_TESTUSDT.trades.csv').open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]['exit_price']), 110.0)
        self.assertTrue(rows[0]['reason_close'].startswith('take profit'))
        self.assertEqual(float(rows[0]['entry_price']), 100.0)
        self.assertEqual(rows[0]['entry_ts'], str(bars[3].ts))

    def test_stop_wins_when_bar_touches_both(self):
        bars = make_bars(overrides={4: (100.0, 112.0, 85.0, 95.0)})
        payload = self.run_with(BuyThenLeave(), bars)
        self.assertEqual(payload['history']['trades'], 1)
        with (self.outdir / 'test_buy_leave_TESTUSDT.trades.csv').open() as fh:
            row = list(csv.DictReader(fh))[0]
        self.assertEqual(float(row['exit_price']), 90.0)
        self.assertTrue(row['reason_close'].startswith('stop loss'))

    def test_time_exit_sells_next_open(self):
        payload = self.run_with(BuyThenTimeExit(), make_bars())
        self.assertEqual(payload['history']['trades'], 1)
        with (self.outdir / 'test_buy_time_TESTUSDT.trades.csv').open() as fh:
            row = list(csv.DictReader(fh))[0]
        self.assertEqual(row['exit_ts'], str(Bar(datetime(2024, 1, 2, 0, tzinfo=timezone.utc), 1, 1, 1, 1).ts))
        self.assertEqual(float(row['exit_price']), 100.0)
        self.assertEqual(row['reason_close'], 'time test')

    def test_second_cycle_keeps_the_pin_and_is_flat(self):
        bars = make_bars(overrides={4: (100.0, 112.0, 99.0, 108.0)})
        first = self.run_with(BuyThenLeave(), bars)
        second = self.run_with(
            BuyThenLeave(), bars, now=datetime(2024, 12, 2, tzinfo=timezone.utc)
        )
        self.assertEqual(first['deploy']['deploy_equity'], second['deploy']['deploy_equity'])
        self.assertEqual(first['deploy']['deployed_at'], second['deploy']['deployed_at'])
        self.assertEqual(second['deploy']['return_pct'], 0.0)
        self.assertIsNone(second['deploy']['repinned_at'])
        with (self.outdir / 'test_buy_leave_TESTUSDT.equity.csv').open() as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 2)  # header + one distinct bar

    def test_window_move_repins_the_deploy_mark(self):
        bars = make_bars(20, overrides={4: (100.0, 112.0, 99.0, 108.0)})
        self.run_with(BuyThenLeave(), bars)
        later = bars[13:]  # window start moves 52h > 2 days
        payload = self.run_with(BuyThenLeave(), later)
        self.assertIsNotNone(payload['deploy']['repinned_at'])
        self.assertEqual(payload['deploy']['return_pct'], 0.0)
        self.assertEqual(payload['deploy']['deployed_at'][:10], '2024-12-01')

    def test_status_file_keeps_other_runners(self):
        self.run_with(BuyThenLeave(), make_bars())
        self.run_with(BuyThenTimeExit(), make_bars())
        state = json.loads((self.outdir / 'status.json').read_text())
        self.assertEqual(set(state['runners']),
                         {'test_buy_leave:TEST/USDT', 'test_buy_time:TEST/USDT'})


if __name__ == '__main__':
    unittest.main()