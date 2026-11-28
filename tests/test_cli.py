import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.cli import build_parser, main


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def test_dryrun_alerts_are_opt_in(self):
        parser = build_parser()
        self.assertFalse(parser.parse_args(["dryrun", "--all"]).alerts)
        self.assertTrue(parser.parse_args(["dryrun", "--all", "--alerts"]).alerts)

    def test_strategies_lists_registered_names(self):
        code, out = run_cli("strategies")
        self.assertEqual(code, 0)
        self.assertIn("sma_cross", out)
        self.assertIn("rsi_meanrev", out)

    def test_gen_data_then_backtest_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = Path(tmp) / "bars.csv"
            code, out = run_cli("gen-data", "--bars", "400", "--out", str(csv))
            self.assertEqual(code, 0)
            self.assertTrue(csv.exists())
            self.assertIn("wrote 400 bars", out)

            runs = Path(tmp) / "runs"
            code, out = run_cli(
                "backtest", "--source", "csv", "--csv", str(csv),
                "--strategy", "sma_cross", "--outdir", str(runs),
            )
            self.assertEqual(code, 0)
            self.assertIn("Total return", out)
            self.assertTrue((runs / "trades.csv").exists())

    def test_deploy_refused_without_promotion(self):
        import os
        from unittest import mock

        from bot import promotions as promo

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(promo, "PROMOTIONS", Path(tmp)), \
             mock.patch.dict(os.environ, {"BOT_ENV": "demo", "BOT_LAB": ""}):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(err):
                code = main(["deploy", "--symbol", "BTC/USDT", "--interval", "4h",
                             "--strategy", "sma_cross", "--once"])
        self.assertEqual(code, 2)
        self.assertIn("not activated for demo", err.getvalue())

    def test_backtest_synthetic_with_params_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_cli(
                "backtest", "--source", "synthetic", "--bars", "600",
                "--strategy", "rsi_meanrev", "--param", "entry=35",
                "--outdir", tmp, "--json",
            )
            self.assertEqual(code, 0)
            payload = json.loads(out[out.index("{", out.index("trades")):])
            self.assertEqual(payload["strategy"], "rsi_meanrev")
            self.assertEqual(payload["params"]["entry"], 35)
            self.assertIn("sharpe", payload["metrics"])

    def test_csv_source_without_path_fails(self):
        with self.assertRaises(SystemExit):
            run_cli("backtest", "--source", "csv")

    def test_bad_param_format_fails(self):
        with self.assertRaises(SystemExit):
            run_cli("backtest", "--param", "fast")

    def test_demo_mode_runs_and_reports_no_real_orders(self):
        if True:
            code, out = run_cli(
                "demo", "--source", "synthetic", "--bars", "300",
                "--strategy", "sma_cross", "--param", "fast=5", "--param", "slow=12",
                "--every", "100",
            )
        self.assertEqual(code, 0)
        self.assertIn("no real orders", out)
        self.assertIn("[demo] finished", out)


if __name__ == "__main__":
    unittest.main()

class ConfigHelperTests(unittest.TestCase):
    def test_config_tolerates_subparsers_without_bars_per_year(self) -> None:
        """The deploy subcommand defines its own args and has no --bars-per-year;
        _config must infer the factor instead of raising AttributeError."""
        import argparse
        from datetime import datetime, timedelta, timezone

        from bot.cli import _config
        from bot.data.feed import Bar

        args = argparse.Namespace(
            initial_cash=10_000.0,
            fee_bps=10.0,
            slippage_bps=5.0,
            risk_per_trade=0.01,
            max_position_pct=0.95,
            max_drawdown_stop=0.25,
        )  # deliberately no bars_per_year
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            Bar(ts=start + timedelta(days=i), open=1.0, high=1.0, low=1.0,
                close=1.0, volume=1.0)
            for i in range(30)
        ]
        cfg = _config(args, bars)
        self.assertAlmostEqual(cfg.bars_per_year, 365.0, delta=1.0)
