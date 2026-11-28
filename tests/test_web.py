"""Offline tests for the web terminal API (no network, no Postgres)."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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
    api_equity,
    api_forward,
    api_presets_create,
    api_presets_delete,
    api_scan,
    api_strategies,
    api_symbols,
    favicon_svg,
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


class _NoSnapshots:
    """Stand-in connection: _run_rate finds no equity rows."""

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return []


class UptimeTests(unittest.TestCase):
    """started_at + uptime_s answer "since when has this bot been running"."""

    def _bot(self, now, **over):
        from datetime import timedelta

        bot = {
            "id": 7, "name": "UNIUSDT-capitulation_dip_uni",
            "symbol": "UNI/USDT", "timeframe": "4h", "strategy_name": "capitulation_dip_uni",
            "status": "running", "capital": 1000.0, "equity": 1000.0, "realized": 0.0,
            "started_at": now - timedelta(days=3, hours=4),
            "last_heartbeat_at": now - timedelta(seconds=42),
        }
        bot.update(over)
        return bot

    def test_live_bot_reports_its_uptime(self) -> None:
        from datetime import datetime, timezone

        from bot.web import _decorate_bot

        now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
        bot = self._bot(now)
        out = _decorate_bot(_NoSnapshots(), bot, now)
        self.assertTrue(out["alive"])
        self.assertEqual(out["uptime_s"], 3 * 86400 + 4 * 3600)
        self.assertEqual(out["started_at"], bot["started_at"])

    def test_dead_loop_has_no_uptime(self) -> None:
        """A stale heartbeat must not be dressed up as "up 3d"."""
        from datetime import datetime, timedelta, timezone

        from bot.web import _decorate_bot

        now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
        out = _decorate_bot(
            _NoSnapshots(), self._bot(now, last_heartbeat_at=now - timedelta(hours=2)), now
        )
        self.assertFalse(out["alive"])
        self.assertIsNone(out["uptime_s"])


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

    def test_forward_reads_the_sleeve_status_file(self) -> None:
        self._manifest()
        state = self.base / "state"
        path = state / "sma_cross" / "forward" / "status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "runners": {"BTC/USDT": {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "history": {"return_pct": 0.5, "trades": 10, "max_drawdown": -0.1},
                "forward": {"return_pct": 0.01, "trades": 1},
                "position": None,
                "last_bar": "2026-11-28 04:00:00+00:00",
            }},
        }), encoding="utf-8")
        data = api_forward(base=self.base, state_dir=state)
        self.assertEqual(len(data["sleeves"]), 1)
        row = data["sleeves"][0]
        self.assertEqual(row["state"], "running")
        self.assertEqual(row["symbol"], "BTC/USDT")
        self.assertEqual(row["history"]["trades"], 10)
        self.assertIsNone(row["position"])
        json.dumps(data)  # the browser must be able to read it

    def test_forward_without_status_is_waiting(self) -> None:
        self._manifest()
        data = api_forward(base=self.base, state_dir=self.base / "none")
        self.assertEqual(data["sleeves"][0]["state"], "waiting")

    def test_forward_hides_an_inactive_manifest(self) -> None:
        self._manifest(active=False)
        data = api_forward(base=self.base, state_dir=self.base / "state")
        self.assertEqual(data["sleeves"], [])

    def test_forward_is_empty_in_prod(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "prod"}):
            data = api_forward(base=self.base, state_dir=self.base / "state")
        self.assertEqual(data["sleeves"], [])

    def test_preset_create_requires_promotion(self) -> None:
        out = api_presets_create({"name": "x", "kind": "sma_cross"})
        self.assertIn("not activated for paper", out["error"])


class _NoRowsConn:
    """Stand-in connection: no equity_snapshots rows for this world."""

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


class EquityCurveTests(unittest.TestCase):
    """The dashboard equity widget merges every wallet into one curve.

    Two honesty rules are pinned here: a wallet that starts later joins at
    its own capital (it must not read as a portfolio loss), and after
    downsampling the final point is still the true current equity.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _contributors(self, contribs):
        db = patch("bot.web._db_conn", return_value=_NoRowsConn())
        src = patch("bot.web._equity_contributors", return_value=contribs)
        db.start(); src.start()
        self.addCleanup(db.stop); self.addCleanup(src.stop)

    def test_late_wallet_joins_at_its_own_capital(self) -> None:
        from datetime import datetime, timedelta, timezone

        t0 = datetime(2026, 11, 28, tzinfo=timezone.utc)
        contribs = [
            ("A", [(t0, 1000.0), (t0 + timedelta(hours=8), 1100.0)]),
            ("B", [(t0 + timedelta(hours=4), 1000.0)]),
        ]
        self._contributors(contribs)
        data = api_equity()
        equity = [point["equity"] for point in data["series"]]
        self.assertEqual(equity, [1000.0, 2000.0, 2100.0])
        self.assertEqual(data["contributors"], 2)
        json.dumps(data)  # the browser must be able to read it

    def test_downsample_keeps_the_true_last_point(self) -> None:
        from datetime import datetime, timedelta, timezone

        t0 = datetime(2026, 11, 28, tzinfo=timezone.utc)
        series = [(t0 + timedelta(minutes=i), 1000.0 + i) for i in range(1000)]
        self._contributors([("A", series)])
        data = api_equity(max_points=50)
        self.assertEqual(len(data["series"]), 50)
        self.assertEqual(data["series"][-1]["equity"], 1999.0)
        # Every bucket keeps a real sample: values never invent a move.
        equity = [point["equity"] for point in data["series"]]
        self.assertEqual(equity, sorted(equity))
        self.assertTrue(all(v in {1000.0 + i for i in range(1000)} for v in equity))

    def test_forward_equity_csv_is_a_contributor(self) -> None:
        from types import SimpleNamespace

        state = self.base / "state"
        path = state / "sma_cross" / "forward" / "BTCUSDT.equity.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "ts,equity,live_return,position_qty\n"
            "2026-11-28 00:00:00+00:00,1000.0,0,0\n"
            "2026-11-28 04:00:00+00:00,1010.0,0.01,1\n"
            "2026-11-28 08:00:00+00:00,1005.0,0.005,1\n",
            encoding="utf-8")
        with patch.dict(os.environ, {"BOT_ENV": "paper", "BOT_LAB": ""}), \
                patch("bot.web._db_conn", return_value=_NoRowsConn()), \
                patch("bot.web.env_state_dir", return_value=state), \
                patch("bot.web.promo.active_manifests",
                      return_value={"sma_cross": SimpleNamespace(symbols=("BTC/USDT",))}):
            data = api_equity()
        self.assertEqual(data["contributors"], 1)
        self.assertEqual(data["series"][-1]["equity"], 1005.0)

    def test_prod_ignores_the_forward_csvs(self) -> None:
        from types import SimpleNamespace

        state = self.base / "state"
        path = state / "sma_cross" / "forward" / "BTCUSDT.equity.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "ts,equity,live_return,position_qty\n"
            "2026-11-28 00:00:00+00:00,1000.0,0,0\n",
            encoding="utf-8")
        with patch.dict(os.environ, {"BOT_ENV": "prod", "BOT_LAB": ""}), \
                patch("bot.web._db_conn", return_value=_NoRowsConn()), \
                patch("bot.web.env_state_dir", return_value=state), \
                patch("bot.web.promo.active_manifests",
                      return_value={"sma_cross": SimpleNamespace(symbols=("BTC/USDT",))}):
            data = api_equity()
        self.assertEqual(data["contributors"], 0)
        self.assertEqual(data["series"], [])

    def test_broken_db_still_serves_an_empty_curve(self) -> None:
        # The widget must not take the dashboard down when the database is
        # unreachable; it renders its own empty state instead.
        def _broken():
            raise ValueError("database not reachable")
        with patch("bot.web._db_conn", side_effect=_broken):
            data = api_equity()
        self.assertEqual(data["series"], [])
        self.assertEqual(data["contributors"], 0)


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

    def test_favicon_is_coloured_for_its_world(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "paper"}):
            paper = favicon_svg().decode("utf-8")
        with patch.dict(os.environ, {"BOT_ENV": "prod"}):
            prod = favicon_svg().decode("utf-8")
        self.assertIn("#0ecb81", paper)          # paper green
        self.assertNotIn("#f6465d", paper)
        self.assertIn("#f6465d", prod)           # production red
        self.assertNotIn("#0ecb81", prod)
        for svg in (paper, prod):
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn('viewBox="0 0 64 64"', svg)
            self.assertNotIn("{{", svg)

    def test_page_links_its_icon(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "paper"}):
            html = render_index().decode("utf-8")
        self.assertIn('<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1">', html)
        self.assertIn('class="logo"><img src="/favicon.svg?v=1"', html)

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



class AccountDustTests(unittest.TestCase):
    """Every balance is listed; the leftovers are flagged, never dropped."""

    class _Broker:
        def __init__(self, balances):
            self._balances = balances

        def balances(self):
            return self._balances

    class _Client:
        def __init__(self, balances, prices):
            self.broker = AccountDustTests._Broker(balances)
            self._prices = prices

        def get_account(self):
            return {"account_number": 7001, "status": "ENABLED", "equity": 10001.0,
                    "cash": 9662.24, "buying_power": 9662.24}

        def get_positions(self):
            return []

        def _price(self, asset):
            return self._prices[asset]

    def _account(self, balances, prices):
        from bot.web import api_account

        client = self._Client(balances, prices)
        with patch("bot.web.BinanceLiveClient", return_value=client):
            return api_account()

    def test_the_crumb_is_flagged_and_real_books_are_not(self) -> None:
        from bot.exchange import Balance

        data = self._account(
            {"USDT": Balance("USDT", 9662.24),
             "XRP": Balance("XRP", 360.4392),
             "JUP": Balance("JUP", 0.0949)},
            {"USDT": 1.0, "XRP": 0.94, "JUP": 0.24},
        )
        flags = {a["asset"]: a["dust"] for a in data["assets"]}
        self.assertEqual(flags, {"USDT": False, "XRP": False, "JUP": True})
        self.assertAlmostEqual(data["dust"]["usd"], 0.022776, places=9)
        self.assertEqual(data["dust"]["count"], 1)
        self.assertEqual(data["dust"]["threshold_usd"], 1.0)

    def test_an_unpriced_asset_is_unknown_not_dust(self) -> None:
        from bot.exchange import Balance

        data = self._account({"USDT": Balance("USDT", 10.0),
                              "ZZZ": Balance("ZZZ", 5.0)}, {"USDT": 1.0})
        zzz = [a for a in data["assets"] if a["asset"] == "ZZZ"][0]
        self.assertIsNone(zzz["value"])
        self.assertFalse(zzz["dust"])
        self.assertEqual(data["dust"]["count"], 0)
        self.assertEqual(data["dust"]["usd"], 0.0)

    def test_an_unreadable_venue_still_reports_the_cutoff(self) -> None:
        from bot.exchange.binance import BrokerError
        from bot.web import api_account

        with patch.dict(os.environ, {"BOT_ENV": "paper"}), \
                patch("bot.web.BinanceLiveClient", side_effect=BrokerError("no key")):
            data = api_account()
        self.assertEqual(data["assets"], [])
        self.assertEqual(data["dust"], {"threshold_usd": 1.0, "count": 0, "usd": 0.0})

    def test_the_page_hides_dust_until_unticked(self) -> None:
        with patch.dict(os.environ, {"BOT_ENV": "paper"}):
            html = render_index().decode("utf-8")
        self.assertIn("localStorage.getItem(LS_DUST)", html)
        self.assertIn("window.setHideDust", html)
        self.assertIn("hide under", html)


if __name__ == "__main__":
    unittest.main()
