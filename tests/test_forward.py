"""Tests for the per-symbol forward runner (simulated replay, no orders)."""

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bot import promotions as promo
from bot.data.feed import Bar
from bot.forward import (
    active_specs, default_outdir, run_all_forever, run_cycle, run_forever, run_specs,
)
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


class BuyAt15(Strategy):
    name = 'test_buy_late'
    warmup = 1
    PARAMS = ()

    def prepare(self, df):
        return df

    def signal(self, i, df, position):
        if position == 0 and i == 15:
            return Signal(Action.BUY, 'late entry', stop_loss=90.0, take_profit=200.0)
        return None


class TestForwardRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, strategy, bars, outdir=None, symbol="TEST/USDT", now=None,
                 notifier=None):
        return run_cycle(
            strategy, symbol, '4h', params={}, capital=1_000.0,
            fee_bps=0.0, slippage_bps=0.0, risk_per_trade=0.05,
            max_position_pct=0.95, max_drawdown_stop=0.9,
            outdir=outdir or self.outdir, bars=bars,
            now=now or datetime(2024, 12, 1, tzinfo=timezone.utc),
            notifier=notifier,
        )

    def test_default_folder_is_per_strategy(self):
        path = default_outdir('capitulation_dip_uni')
        self.assertEqual(path.name, 'forward')
        self.assertEqual(path.parent.name, 'capitulation_dip_uni')

    def test_target_exit_is_honoured(self):
        bars = make_bars(overrides={4: (100.0, 112.0, 99.0, 108.0)})
        payload = self.run_with(BuyThenLeave(), bars)
        self.assertIsNone(payload['position'])
        self.assertEqual(payload['history']['trades'], 1)
        # The deploy pin is taken on this first cycle, so the public forward
        # wallet starts at capital even though history has already traded.
        self.assertAlmostEqual(payload['forward']['equity'], 1_000.0)
        self.assertAlmostEqual(payload['forward']['wallet'], 1_000.0)
        self.assertGreater(payload['history']['equity'], 1_000.0)
        with (self.outdir / 'TESTUSDT.trades.csv').open() as fh:
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
        with (self.outdir / 'TESTUSDT.trades.csv').open() as fh:
            row = list(csv.DictReader(fh))[0]
        self.assertEqual(float(row['exit_price']), 90.0)
        self.assertTrue(row['reason_close'].startswith('stop loss'))

    def test_time_exit_sells_next_open(self):
        payload = self.run_with(BuyThenTimeExit(), make_bars())
        self.assertEqual(payload['history']['trades'], 1)
        with (self.outdir / 'TESTUSDT.trades.csv').open() as fh:
            row = list(csv.DictReader(fh))[0]
        expected = str(Bar(datetime(2024, 1, 2, 0, tzinfo=timezone.utc), 1, 1, 1, 1).ts)
        self.assertEqual(row['exit_ts'], expected)
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
        with (self.outdir / 'TESTUSDT.equity.csv').open() as fh:
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

    def test_new_forward_fill_is_alerted_once(self):
        class Capture:
            enabled = True

            def __init__(self):
                self.calls = []

            def send_trade(self, event):
                self.calls.append(dict(event))
                return True

        capture = Capture()
        first = self.run_with(BuyAt15(), make_bars(16), notifier=capture)
        self.assertEqual(capture.calls, [])
        self.assertTrue(first['alerts']['initialized'])
        self.assertEqual(first['alerts']['last_fill'], '')

        second = self.run_with(BuyAt15(), make_bars(20), notifier=capture)
        self.assertEqual(len(capture.calls), 1)
        event = capture.calls[0]
        self.assertEqual(event['side'], 'buy')
        self.assertEqual(event['price'], 100.0)
        self.assertEqual(event['stop'], 90.0)
        self.assertEqual(event['take_profit'], 200.0)
        self.assertTrue(second['alerts']['last_fill'])

        third = self.run_with(BuyAt15(), make_bars(20), notifier=capture)
        self.assertEqual(len(capture.calls), 1)
        self.assertEqual(third['alerts']['last_fill'], second['alerts']['last_fill'])

    def test_status_file_keeps_other_symbols(self):
        self.run_with(BuyThenLeave(), make_bars(), symbol="A/USDT")
        self.run_with(BuyThenLeave(), make_bars(), symbol="B/USDT")
        state = json.loads((self.outdir / 'status.json').read_text())
        self.assertEqual(state['strategy'], 'test_buy_leave')
        self.assertEqual(set(state['runners']), {'A/USDT', 'B/USDT'})

    def test_status_records_the_risk_profile(self):
        payload = self.run_with(BuyThenLeave(), make_bars())
        self.assertEqual(payload["risk"]["risk_per_trade"], 0.05)
        self.assertEqual(payload["risk"]["max_position_pct"], 0.95)
        self.assertEqual(payload["risk"]["max_drawdown_stop"], 0.9)
        self.assertEqual(payload["risk"]["source"], "flags")

    def test_same_profile_keeps_the_pin(self):
        self.run_with(BuyThenLeave(), make_bars())
        first = json.loads((self.outdir / "status.json").read_text())
        first = first["runners"]["TEST/USDT"]
        again = self.run_with(BuyThenLeave(), make_bars(),
                              now=datetime(2024, 12, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(again["deploy"]["deploy_equity"], first["deploy"]["deploy_equity"])
        self.assertIsNone(again["deploy"]["repinned_at"])

    def test_changed_profile_repins_the_deploy_mark(self):
        self.run_with(BuyThenLeave(), make_bars())
        first = json.loads((self.outdir / "status.json").read_text())
        first = first["runners"]["TEST/USDT"]
        shrunk = run_cycle(
            BuyThenLeave(), "TEST/USDT", "4h", params={}, capital=1_000.0,
            fee_bps=0.0, slippage_bps=0.0, risk_per_trade=0.02,
            max_position_pct=0.95, max_drawdown_stop=0.9, outdir=self.outdir,
            bars=make_bars(), now=datetime(2024, 12, 1, 2, tzinfo=timezone.utc),
        )
        # a smaller size is a different book, not a -60% forward record
        self.assertEqual(shrunk["deploy"]["deploy_equity"], shrunk["history"]["equity"])
        self.assertEqual(shrunk["deploy"]["repinned_at"], first["deploy"]["deployed_at"])
        self.assertEqual(shrunk["risk"]["risk_per_trade"], 0.02)

    def test_a_status_without_a_profile_repins_once(self):
        self.run_with(BuyThenLeave(), make_bars())
        path = self.outdir / "status.json"
        state = json.loads(path.read_text())
        state["runners"]["TEST/USDT"].pop("risk")
        path.write_text(json.dumps(state))
        again = self.run_with(BuyThenLeave(), make_bars(),
                              now=datetime(2024, 12, 1, 3, tzinfo=timezone.utc))
        self.assertIsNotNone(again["deploy"]["repinned_at"])

    def test_one_folder_cannot_hold_two_strategies(self):
        self.run_with(BuyThenLeave(), make_bars())
        with self.assertRaises(RuntimeError):
            self.run_with(BuyThenTimeExit(), make_bars())


class TestActiveSpecs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "paper").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, **fields):
        payload = {
            "strategy": name, "env": "paper", "params_hash": "sha256:x",
            "symbols": ["A/USDT"], "timeframe": "4h", "capital": 1000.0,
            "promoted_at": "2026-11-28T00:00:00+00:00",
        }
        payload.update(fields)
        (self.base / "paper" / f"{name}.json").write_text(json.dumps(payload))

    def test_reads_every_symbol_of_every_active_manifest(self):
        self.write("beta", symbols=["B/USDT", "C/USDT"], capital=2000.0)
        self.write("alpha", symbols=["A/USDT"])
        self.write("dead", symbols=["D/USDT"], active=False)
        specs = active_specs("paper", base=self.base)
        self.assertEqual([s["strategy"] for s in specs], ["alpha", "beta", "beta"])
        self.assertEqual([s["symbol"] for s in specs], ["A/USDT", "B/USDT", "C/USDT"])
        self.assertEqual(specs[1]["capital"], 2000.0)

    def test_spec_carries_the_measured_risk_profile(self):
        self.write("alpha", evidence={
            "risk_per_trade": 0.05, "max_position_pct": 0.5, "max_drawdown_stop": 0.1,
        })
        self.write("beta")
        specs = {s["strategy"]: s for s in active_specs("paper", base=self.base)}
        self.assertEqual(specs["alpha"]["risk_per_trade"], 0.05)
        self.assertEqual(specs["alpha"]["max_position_pct"], 0.5)
        self.assertEqual(specs["alpha"]["max_drawdown_stop"], 0.1)
        # an older manifest states nothing: the runner keeps its own flags
        self.assertNotIn("risk_per_trade", specs["beta"])


class TestRunSpecs(unittest.TestCase):
    SPEC = {"strategy": "s", "symbol": "A/USDT", "timeframe": "4h",
            "capital": 1000.0, "params_hash": promo.params_hash({})}

    def test_one_broken_sleeve_does_not_stop_the_rest(self):
        fake = SimpleNamespace(name="s", params={})
        with mock.patch("bot.forward.build", side_effect=[RuntimeError("boom"), fake]), \
                mock.patch("bot.forward.run_cycle", return_value={}) as cycle, \
                mock.patch("bot.forward.format_status", return_value="ok"), \
                mock.patch("sys.stdout"):
            results = run_specs([self.SPEC, self.SPEC])
        self.assertEqual(cycle.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertIn("boom", results[0]["error"])

    def test_manifest_profile_wins_over_the_service_flags(self):
        fake = SimpleNamespace(name="s", params={})
        spec = dict(self.SPEC, risk_per_trade=0.05, max_position_pct=0.5,
                    max_drawdown_stop=0.1)
        with mock.patch("bot.forward.build", return_value=fake), \
                mock.patch("bot.forward.run_cycle", return_value={}) as cycle, \
                mock.patch("bot.forward.format_status", return_value="ok"), \
                mock.patch("sys.stdout"):
            run_specs([spec], risk_per_trade=0.03, max_position_pct=0.95,
                      max_drawdown_stop=0.99)
        kwargs = cycle.call_args.kwargs
        self.assertEqual(kwargs["risk_per_trade"], 0.05)
        self.assertEqual(kwargs["max_position_pct"], 0.5)
        self.assertEqual(kwargs["max_drawdown_stop"], 0.1)
        self.assertEqual(kwargs["profile_source"], "manifest")

    def test_manifest_without_a_profile_uses_the_flags(self):
        fake = SimpleNamespace(name="s", params={})
        with mock.patch("bot.forward.build", return_value=fake), \
                mock.patch("bot.forward.run_cycle", return_value={}) as cycle, \
                mock.patch("bot.forward.format_status", return_value="ok"), \
                mock.patch("sys.stdout"):
            run_specs([self.SPEC], risk_per_trade=0.03, max_position_pct=0.95,
                      max_drawdown_stop=0.99)
        kwargs = cycle.call_args.kwargs
        self.assertEqual(kwargs["risk_per_trade"], 0.03)
        self.assertEqual(kwargs["max_drawdown_stop"], 0.99)
        self.assertEqual(kwargs["profile_source"], "flags")

    def test_hash_mismatch_is_refused_before_replaying(self):
        fake = SimpleNamespace(name="s", params={})
        bad = dict(self.SPEC, params_hash="sha256:other")
        with mock.patch("bot.forward.build", return_value=fake), \
                mock.patch("bot.forward.run_cycle") as cycle, \
                mock.patch("sys.stdout"):
            results = run_specs([bad])
        cycle.assert_not_called()
        self.assertIn("promote the change", results[0]["error"])



class TestForwardLoopHeartbeat(unittest.TestCase):
    def test_all_loop_pings_once_per_pass_even_with_no_sleeves(self):
        heartbeat = mock.Mock()
        notifier = mock.Mock(enabled=False)
        with mock.patch("bot.forward.Heartbeat", return_value=heartbeat) as hb_cls, \
                mock.patch("bot.forward.Notifier.from_env", return_value=notifier), \
                mock.patch("bot.forward.active_specs", return_value=[]), \
                mock.patch("bot.forward.run_specs") as specs, \
                mock.patch("bot.forward.time.sleep", side_effect=KeyboardInterrupt), \
                mock.patch("sys.stdout"):
            with self.assertRaises(KeyboardInterrupt):
                run_all_forever(30, env="paper", heartbeat_url="https://hc.example/ping")
        hb_cls.assert_called_once_with("https://hc.example/ping", every=1800.0)
        heartbeat.ping.assert_called_once_with()
        specs.assert_called_once_with([], notifier=notifier)

    def test_single_loop_pings_after_the_cycle(self):
        heartbeat = mock.Mock()
        with mock.patch("bot.forward.Heartbeat", return_value=heartbeat), \
                mock.patch("bot.forward.run_cycle", return_value={"ok": True}), \
                mock.patch("bot.forward.format_status", return_value="ok"), \
                mock.patch("bot.forward.time.sleep", side_effect=KeyboardInterrupt), \
                mock.patch("sys.stdout"):
            with self.assertRaises(KeyboardInterrupt):
                run_forever(30, strategy_name="s", symbol="A/USDT")
        heartbeat.ping.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
