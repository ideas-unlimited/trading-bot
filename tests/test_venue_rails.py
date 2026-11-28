"""Rails that keep the demo venue out of live and per-world keys apart."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.envdot import load_env_for
from bot.exchange import order_client as lc


class LiveRefusesDemoTests(unittest.TestCase):
    def test_prod_refuses_before_touching_credentials(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "live"}):
            with self.assertRaises(lc.BrokerError) as ctx:
                lc.BinanceOrderClient()
        message = str(ctx.exception)
        self.assertIn("LIVE refused", message)
        self.assertIn("Demo credentials are never accepted", message)

    def test_prod_cannot_inject_a_broker_either(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "live"}):
            with self.assertRaises(lc.BrokerError):
                lc.BinanceOrderClient(broker=object())

    def test_demo_env_uses_the_demo_venue(self):
        env = {"BOT_ENV": "demo", "BINANCE_DEMO_KEY": "k", "BINANCE_DEMO_SECRET": "s"}
        with mock.patch.dict(os.environ, env):
            client = lc.BinanceOrderClient()
        self.assertTrue(client.is_demo)
        self.assertEqual(client.env, "demo")


class VenueRulesTests(unittest.TestCase):
    def test_filters_reach_the_broker_as_the_pairs_own_rules(self):
        """The loop needs the lot step to tell a book from unsellable dust."""
        sentinel = object()
        broker = mock.Mock()
        broker.filters.return_value = sentinel
        client = lc.BinanceOrderClient(broker=broker, env="demo")
        self.assertIs(client.filters("JUP/USDT"), sentinel)
        broker.filters.assert_called_once_with("JUPUSDT")


class PerEnvFileTests(unittest.TestCase):
    def test_specific_file_wins_over_base(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / ".env"
            base.write_text("FOO=base\nBAR=base\n", encoding="utf-8")
            (Path(td) / ".env.live").write_text("FOO=live\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FOO", None)
                os.environ.pop("BAR", None)
                load_env_for("live", base=base)
                self.assertEqual(os.environ.get("FOO"), "live")
                self.assertEqual(os.environ.get("BAR"), "base")

    def test_process_environment_beats_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / ".env"
            base.write_text("FOO=file\n", encoding="utf-8")
            (Path(td) / ".env.demo").write_text("FOO=specific\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"FOO": "shell"}):
                load_env_for("demo", base=base)
                self.assertEqual(os.environ["FOO"], "shell")


if __name__ == "__main__":
    unittest.main()