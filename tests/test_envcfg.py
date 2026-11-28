"""Environment separation: paper vs prod database names, paths and ports."""

import os
import unittest
from unittest import mock

from bot import envcfg

BASE = "postgresql://postgres:secret@localhost:5432/trading_bot"


class EnvNameTests(unittest.TestCase):
    def test_defaults_to_paper(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOT_ENV", None)
            self.assertEqual(envcfg.env_name(), "paper")

    def test_reads_prod(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "prod"}):
            self.assertEqual(envcfg.env_name(), "prod")

    def test_rejects_unknown(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "staging"}):
            with self.assertRaises(ValueError):
                envcfg.env_name()


class DatabaseUrlTests(unittest.TestCase):
    def test_swaps_database_name(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": BASE, "BOT_ENV": "prod"}):
            for key in ("DATABASE_URL_COMMON", "DATABASE_URL_PAPER", "DATABASE_URL_PROD"):
                os.environ.pop(key, None)
            self.assertEqual(envcfg.database_url("common"), BASE.replace("trading_bot", "trading_common"))
            self.assertEqual(envcfg.database_url("paper"), BASE.replace("trading_bot", "trading_paper"))
            self.assertEqual(envcfg.database_url("prod"), BASE.replace("trading_bot", "trading_prod"))
            self.assertEqual(envcfg.database_url(), BASE.replace("trading_bot", "trading_prod"))

    def test_explicit_override_wins(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": BASE,
                                          "DATABASE_URL_PROD": "postgresql://x@h:1/custom"}):
            self.assertEqual(envcfg.database_url("prod"), "postgresql://x@h:1/custom")

    def test_missing_base_returns_none(self):
        with mock.patch("bot.envcfg.load_env", lambda: None), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            for key in ("DATABASE_URL_COMMON", "DATABASE_URL_PAPER", "DATABASE_URL_PROD"):
                os.environ.pop(key, None)
            self.assertIsNone(envcfg.database_url("common"))

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            envcfg.database_name("staging")


class PathsTests(unittest.TestCase):
    def test_ports_differ(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOT_WEB_PORT", None)
            self.assertEqual(envcfg.web_port("paper"), 9000)
            self.assertEqual(envcfg.web_port("prod"), 9001)

    def test_dirs_are_per_environment(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "prod"}, clear=False):
            for key in ("BOT_STATE_DIR", "BOT_LOG_DIR"):
                os.environ.pop(key, None)
            self.assertEqual(envcfg.state_dir().name, "prod")
            self.assertEqual(envcfg.log_dir().name, "prod")
            self.assertNotEqual(envcfg.state_dir("paper"), envcfg.state_dir("prod"))

    def test_lab_mode_is_opt_in(self):
        with mock.patch.dict(os.environ, {"BOT_LAB": "1"}):
            self.assertTrue(envcfg.lab_mode())
        with mock.patch.dict(os.environ, {"BOT_LAB": ""}):
            self.assertFalse(envcfg.lab_mode())

    def test_describe(self):
        with mock.patch.dict(os.environ, {"BOT_ENV": "paper", "DATABASE_URL": BASE}):
            info = envcfg.describe()
            self.assertEqual(info["env"], "paper")
            self.assertEqual(info["database"], "trading_paper")
            self.assertEqual(info["common_database"], "trading_common")
            self.assertEqual(info["web_port"], 9000)


if __name__ == "__main__":
    unittest.main()
