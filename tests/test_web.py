"""Offline tests for the web terminal API (no network, no Postgres)."""

import os
import unittest
from unittest.mock import patch

from bot.web import (
    SYMBOLS,
    _jsonify,
    api_backtest,
    api_bots_create,
    api_bots_delete,
    api_presets_create,
    api_presets_delete,
    api_scan,
    api_strategies,
    api_symbols,
    render_index,
)


class JsonifyTests(unittest.TestCase):
    def test_nan_and_datetime(self) -> None:
        from datetime import datetime, timezone

        out = _jsonify({"a": float("nan"), "b": [1.0, float("nan")],
                        "c": datetime(2024, 1, 1, tzinfo=timezone.utc)})
        self.assertIsNone(out["a"])
        self.assertEqual(out["b"], [1.0, None])
        self.assertEqual(out["c"], "2024-01-01T00:00:00+00:00")


class ApiBasicsTests(unittest.TestCase):
    def test_strategies_registry(self) -> None:
        data = api_strategies()
        self.assertIn("sma_cross", data)
        self.assertIn("fast", data["sma_cross"]["params"])

    def test_symbols_dropdowns(self) -> None:
        data = api_symbols()
        crypto_syms = [e["symbol"] for e in data["crypto"]]
        self.assertIn("BTC/USDT", crypto_syms)
        self.assertIn("BNB/USDT", crypto_syms)          # watchlist, TV-only
        self.assertIn("BTC.D", data["indices"][0]["symbol"])
        self.assertNotIn("stocks", data)                # spot crypto only
        self.assertIn("XAUT/USD", [e["symbol"] for e in data["commodities"]])
        bnb = next(e for e in data["crypto"] if e["symbol"] == "BNB/USDT")
        self.assertEqual(bnb["tv"], "BINANCE:BNBUSDT")

    def test_backtest_synthetic_demo(self) -> None:
        payload = {"source": "synthetic", "symbol": "DEMO", "interval": "1h",
                   "strategy": "sma_cross", "params": {"fast": 5, "slow": 10}}
        result = api_backtest(payload)
        self.assertNotIn("error", result)
        self.assertGreater(result["bars"], 0)
        self.assertIn("total_return", result["metrics"])
        self.assertTrue(result["equity_curve"])

    def test_backtest_bad_params(self) -> None:
        result = api_backtest({"source": "synthetic", "strategy": "sma_cross",
                               "params": {"bogus": 1}})
        self.assertIn("error", result)

    def test_scan_synthetic(self) -> None:
        result = api_scan({"source": "synthetic", "symbols": ["DEMO"],
                           "timeframes": ["1h"], "strategies": ["sma_cross", "rsi_meanrev"],
                           "params": {"fast": 5, "slow": 10}})
        self.assertIn("rows", result)
        self.assertEqual(len(result["rows"]), 2)
        self.assertIn("sharpe", result["rows"][0])


class EnvironmentBrandingTests(unittest.TestCase):
    """The same HTML serves both worlds; the world must be burned in."""

    def test_paper_page_is_marked_paper(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "paper"}):
            html = render_index().decode("utf-8")
        self.assertIn("<title>trading-bot · paper · terminal</title>", html)
        self.assertIn('<body class="paper">', html)
        self.assertIn("PAPER", html)
        self.assertIn("simulated fills", html)
        self.assertIn('const ENV = "paper"', html)
        self.assertNotIn("{{ENV", html)

    def test_prod_page_is_marked_production(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "prod"}):
            html = render_index().decode("utf-8")
        self.assertIn("PRODUCTION", html)
        self.assertIn('<body class="prod">', html)
        self.assertIn('const ENV = "prod"', html)
        self.assertNotIn("{{ENV", html)

    def test_prod_write_endpoints_are_read_only(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "prod"}):
            results = [
                api_bots_create({"name": "x", "symbol": "BTC/USDT", "timeframe": "1h"}),
                api_bots_delete(1),
                api_presets_create({"name": "x", "kind": "sma_cross"}),
                api_presets_delete(1),
            ]
        for result in results:
            self.assertIn("read-only", result["error"])


if __name__ == "__main__":
    unittest.main()
