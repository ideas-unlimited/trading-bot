"""Rails that keep the demo venue out of prod and per-world keys apart."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.envdot import load_env_for
from bot.exchange import live_client as lc


class ProdRefusesDemoTests(unittest.TestCase):
    def test_prod_refuses_before_touching_credentials(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "prod"}):
            with self.assertRaises(lc.BrokerError) as ctx:
                lc.BinanceLiveClient()
        message = str(ctx.exception)
        self.assertIn("PROD refused", message)
        self.assertIn("Demo credentials are never accepted", message)

    def test_prod_cannot_inject_a_broker_either(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "prod"}):
            with self.assertRaises(lc.BrokerError):
                lc.BinanceLiveClient(broker=object())

    def test_paper_still_uses_the_demo_venue(self):
        env = {"BOT_ENV": "paper", "BINANCE_DEMO_KEY": "k", "BINANCE_DEMO_SECRET": "s"}
        with mock.patch.dict(os.environ, env):
            client = lc.BinanceLiveClient()
        self.assertTrue(client.is_demo)
        self.assertEqual(client.env, "paper")


class PerEnvFileTests(unittest.TestCase):
    def test_specific_file_wins_over_base(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / ".env"
            base.write_text("FOO=base\nBAR=base\n", encoding="utf-8")
            (Path(td) / ".env.prod").write_text("FOO=prod\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FOO", None)
                os.environ.pop("BAR", None)
                load_env_for("prod", base=base)
                self.assertEqual(os.environ.get("FOO"), "prod")
                self.assertEqual(os.environ.get("BAR"), "base")

    def test_process_environment_beats_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / ".env"
            base.write_text("FOO=file\n", encoding="utf-8")
            (Path(td) / ".env.paper").write_text("FOO=specific\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"FOO": "shell"}):
                load_env_for("paper", base=base)
                self.assertEqual(os.environ["FOO"], "shell")


if __name__ == "__main__":
    unittest.main()