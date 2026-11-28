"""Environment separation: demo vs live database names, paths and ports."""

import os
import unittest
from unittest import mock

from bot import envcfg

BASE = "postgresql://postgres:secret@localhost:5432/trading_bot"


class EnvNameTests(unittest.TestCase):
    def test_defaults_to_demo(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOT_ENV", None)
            self.assertEqual(envcfg.env_name(), "demo")

    def test_reads_live(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "live"}):
            self.assertEqual(envcfg.env_name(), "live")

    def test_rejects_unknown(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "staging"}):
            with self.assertRaises(ValueError):
                envcfg.env_name()


class DatabaseUrlTests(unittest.TestCase):
    def test_swaps_database_name(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": BASE, "BOT_ENV": "live"}):
            for key in ("DATABASE_URL_COMMON", "DATABASE_URL_DEMO", "DATABASE_URL_LIVE"):
                os.environ.pop(key, None)
            self.assertEqual(envcfg.database_url("common"), BASE.replace("trading_bot", "trading_common"))
            self.assertEqual(envcfg.database_url("demo"), BASE.replace("trading_bot", "trading_demo"))
            self.assertEqual(envcfg.database_url("live"), BASE.replace("trading_bot", "trading_live"))
            self.assertEqual(envcfg.database_url(), BASE.replace("trading_bot", "trading_live"))

    def test_explicit_override_wins(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": BASE,
                                          "DATABASE_URL_LIVE": "postgresql://x@h:1/custom"}):
            self.assertEqual(envcfg.database_url("live"), "postgresql://x@h:1/custom")

    def test_missing_base_returns_none(self):
        with mock.patch("bot.envcfg.load_env", lambda: None), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            for key in ("DATABASE_URL_COMMON", "DATABASE_URL_DEMO", "DATABASE_URL_LIVE"):
                os.environ.pop(key, None)
            self.assertIsNone(envcfg.database_url("common"))

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            envcfg.database_name("staging")


class PathsTests(unittest.TestCase):
    def test_ports_differ(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOT_WEB_PORT", None)
            self.assertEqual(envcfg.web_port("demo"), 9000)
            self.assertEqual(envcfg.web_port("live"), 9001)

    def test_dirs_are_per_environment(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "live"}, clear=False):
            for key in ("BOT_STATE_DIR", "BOT_LOG_DIR"):
                os.environ.pop(key, None)
            self.assertEqual(envcfg.state_dir().name, "live")
            self.assertEqual(envcfg.log_dir().name, "live")
            self.assertNotEqual(envcfg.state_dir("demo"), envcfg.state_dir("live"))

    def test_lab_mode_is_opt_in(self):
        with mock.patch.dict(os.environ, {"BOT_LAB": "1"}):
            self.assertTrue(envcfg.lab_mode())
        with mock.patch.dict(os.environ, {"BOT_LAB": ""}):
            self.assertFalse(envcfg.lab_mode())

    def test_describe(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "demo", "DATABASE_URL": BASE}):
            info = envcfg.describe()
            self.assertEqual(info["env"], "demo")
            self.assertEqual(info["database"], "trading_demo")
            self.assertEqual(info["common_database"], "trading_common")
            self.assertEqual(info["web_port"], 9000)


if __name__ == "__main__":
    unittest.main()
