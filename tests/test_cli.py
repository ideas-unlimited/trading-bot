import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.cli import main


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
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

    def test_paper_mode_runs_and_reports_no_real_orders(self):
        if True:
            code, out = run_cli(
                "paper", "--source", "synthetic", "--bars", "300",
                "--strategy", "sma_cross", "--param", "fast=5", "--param", "slow=12",
                "--every", "100",
            )
        self.assertEqual(code, 0)
        self.assertIn("no real orders", out)
        self.assertIn("[paper] finished", out)


if __name__ == "__main__":
    unittest.main()
