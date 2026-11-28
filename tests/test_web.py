"""Offline tests for the web terminal API (no network, no Postgres)."""

import unittest

from bot.web import (
    SYMBOLS,
    _jsonify,
    api_backtest,
    api_scan,
    api_strategies,
    api_symbols,
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


if __name__ == "__main__":
    unittest.main()
