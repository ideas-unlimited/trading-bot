"""Offline tests for the web terminal API (no network, no Postgres)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import promotions as promo
from bot.web import (
    SYMBOLS,
    _jsonify,
    api_backtest,
    api_bot_halt,
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

    def test_decimal_is_a_number(self) -> None:
        """Postgres NUMERIC is a Decimal; json.dumps refuses it (broke /api/bots)."""
        from decimal import Decimal

        out = _jsonify({"capital": Decimal("1000.00"), "equity": Decimal("1000.5"),
                        "nested": [Decimal("1.25")]})
        self.assertEqual(out, {"capital": 1000.0, "equity": 1000.5, "nested": [1.25]})
        json.dumps(out)  # what _send_json does next must not raise

    def test_send_json_tolerates_a_stray_decimal(self) -> None:
        """A payload built elsewhere must not 500 the page over one field."""
        from decimal import Decimal

        from bot.web import _json_default

        self.assertEqual(json.loads(json.dumps({"x": Decimal("2.5")},
                                               default=_json_default)), {"x": 2.5})


class ApiBasicsTests(unittest.TestCase):
    def test_strategies_registry_in_lab(self) -> None:
        with patch.dict(os.environ, {"BOT_LAB": "1"}):
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


class KillSwitchApiTests(unittest.TestCase):
    """The kill switch must reject a malformed request before touching the DB."""

    def test_halt_needs_an_explicit_boolean(self) -> None:
        self.assertIn("error", api_bot_halt(7, {}))
        self.assertIn("error", api_bot_halt(7, {"reason": "because"}))


class StrategyCatalogueTests(unittest.TestCase):
    """Paper and prod list promoted strategies only; lab lists the registry."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self._env = patch.dict(os.environ, {"BOT_ENV": "paper", "BOT_LAB": ""})
        self._env.start()
        self.addCleanup(self._env.stop)
        self._promos = patch.object(promo, "PROMOTIONS", self.base)
        self._promos.start()
        self.addCleanup(self._promos.stop)

    def _manifest(self, name: str = "sma_cross", *, env: str = "paper",
                  active: bool = True, symbols: tuple[str, ...] = ("BTC/USDT",),
                  timeframe: str = "4h") -> None:
        path = self.base / env / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "strategy": name, "env": env, "symbols": list(symbols),
            "timeframe": timeframe, "capital": 1000,
            "params_hash": promo.params_hash({}), "active": active,
            "promoted_at": "2026-11-28T00:00:00+00:00",
        }), encoding="utf-8")

    def test_paper_without_manifests_is_empty(self) -> None:
        self.assertEqual(api_strategies(base=self.base), {})

    def test_paper_lists_only_the_promoted_strategy(self) -> None:
        self._manifest()
        data = api_strategies(base=self.base)
        self.assertEqual(set(data), {"sma_cross"})
        self.assertEqual(data["sma_cross"]["symbols"], ["BTC/USDT"])
        self.assertEqual(data["sma_cross"]["timeframe"], "4h")
        self.assertEqual(data["sma_cross"]["capital"], 1000.0)

    def test_inactive_manifest_is_hidden(self) -> None:
        self._manifest(active=False)
        self.assertEqual(api_strategies(base=self.base), {})

    def test_unknown_manifest_name_is_ignored(self) -> None:
        self._manifest("does_not_exist")
        self.assertEqual(api_strategies(base=self.base), {})

    def test_lab_lists_registry_regardless_of_manifests(self) -> None:
        with patch.dict(os.environ, {"BOT_LAB": "1"}):
            data = api_strategies(base=self.base)
        self.assertIn("sma_cross", data)
        self.assertNotIn("timeframe", data["sma_cross"])

    def test_bot_create_requires_promotion(self) -> None:
        out = api_bots_create({"name": "x", "symbol": "BTC/USDT",
                               "timeframe": "4h", "kind": "sma_cross"})
        self.assertIn("not activated for paper", out["error"])

    def test_bot_create_respects_promoted_symbol_and_timeframe(self) -> None:
        self._manifest(symbols=("ETH/USDT",), timeframe="4h")
        wrong_symbol = api_bots_create({"name": "x", "symbol": "BTC/USDT",
                                        "timeframe": "4h", "kind": "sma_cross"})
        self.assertIn("ETH/USDT", wrong_symbol["error"])
        wrong_tf = api_bots_create({"name": "x", "symbol": "ETH/USDT",
                                    "timeframe": "1h", "kind": "sma_cross"})
        self.assertIn("promoted on 4h", wrong_tf["error"])

    def test_preset_create_requires_promotion(self) -> None:
        out = api_presets_create({"name": "x", "kind": "sma_cross"})
        self.assertIn("not activated for paper", out["error"])


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
