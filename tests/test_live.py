"""Offline tests for the Alpaca client helpers and the live paper-trading loop."""

import io
import json
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.alpaca_client import AlpacaClient, bars_from_payload, resolve_timeframe, symbol_is_crypto
from bot.config import BotConfig
from bot.heartbeat import Heartbeat
from bot.live import LiveTrader, compute_entry_qty
from bot.strategy import build


def _payload() -> dict:
    def row(day: int, o: float, h: float, l: float, c: float) -> dict:
        return {
            "t": f"2024-01-{day:02d}T00:00:00Z",
            "o": o, "h": h, "l": l, "c": c, "v": 1000.0,
        }

    bars = [
        row(1, 100, 105, 99, 104),
        row(2, 104, 106, 100, 103),
        row(3, 103, 107, 102, 106),
        row(4, 106, 110, 105, 109),
    ]
    return {"bars": bars, "next_page_token": None}


class _FakeClient:
    """Scripted stand-in for AlpacaClient."""

    def __init__(self, payload: dict, price: float = 109.0):
        self.payload = payload
        self.orders: list[dict] = []
        self.closed: list[str] = []
        self._price = price

    def fetch_bars(self, symbol, timeframe, limit=400, **kwargs):
        return bars_from_payload(self.payload)

    def get_account(self):
        return {"equity": 10_000.0, "cash": 10_000.0}

    def get_position(self, symbol):
        for order in reversed(self.orders):
            if order["side"] == "buy":
                return {"qty": order["qty"]}
        return None

    def get_positions(self):
        return []

    def submit_order(self, symbol, side, qty, **kwargs):
        self.orders.append({"symbol": symbol, "side": side, "qty": qty, **kwargs})
        return {"id": "test-order"}

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {}


class AlpacaHelpersTests(unittest.TestCase):
    def test_timeframes(self) -> None:
        self.assertEqual(resolve_timeframe("1d"), "1Day")
        self.assertEqual(resolve_timeframe("1h"), "1Hour")
        with self.assertRaises(ValueError):
            resolve_timeframe("9h")

    def test_crypto_detection(self) -> None:
        self.assertTrue(symbol_is_crypto("BTC/USD"))
        self.assertFalse(symbol_is_crypto("SPY"))

    def test_bars_from_payload(self) -> None:
        bars = bars_from_payload(_payload())
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars[0].ts, datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(bars[-1].close, 109.0)


class EntrySizingTests(unittest.TestCase):
    def test_stop_sized(self) -> None:
        qty = compute_entry_qty(10_000, 100, 95, risk_per_trade=0.01, max_position_pct=0.95)
        self.assertEqual(qty, 20.0)

    def test_capped_without_stop(self) -> None:
        qty = compute_entry_qty(10_000, 100, None, risk_per_trade=0.01, max_position_pct=0.5)
        self.assertEqual(qty, 50.0)


class LiveTraderTests(unittest.TestCase):
    def _make(self, tmp: Path, strategy_name: str = "sma_cross") -> tuple[LiveTrader, _FakeClient]:
        client = _FakeClient(_payload())
        config = BotConfig(initial_cash=10_000.0)
        trader = LiveTrader(
            client,
            build(strategy_name),
            config,
            "SPY",
            "1d",
            state_path=tmp / "state.json",
            poll=60,
            lookback=300,
            stream=io.StringIO(),
            clock=lambda s: None,
        )
        return trader, client

    def test_no_signal_on_insufficient_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._make(tmp)
            # sma_cross needs warmup(max(50,14)+1)=51 bars; we have 4 -> no crash, nothing sent
            n = trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(n, 0)
            self.assertEqual(client.orders, [])

    def test_buy_order_with_bracket_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_payload())
            bars = bars_from_payload(_payload())
            # Dip then recover, so the fast SMA actually crosses above the slow one.
            base = datetime(2024, 1, 5, tzinfo=timezone.utc)

            def price(k: int) -> float:
                return 104.0 - k if k < 60 else 44.0 + (k - 60)

            extra = [
                {
                    "t": (base + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
                    "o": price(k), "h": price(k) + 1.0, "l": price(k) - 1.0, "c": price(k), "v": 1000.0,
                }
                for k in range(120)
            ]
            client.payload = {"bars": _payload()["bars"] + extra}
            # Seed the state so the loop treats the history as a backlog to
            # process (a fresh state now anchors instead of replaying).
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": bars[0].ts.isoformat(), "stops": {}})
            )
            trader = LiveTrader(
                client, build("sma_cross"), BotConfig(), "SPY", "1d",
                state_path=tmp / "state.json", max_catchup=1000,
                stream=io.StringIO(), clock=lambda s: None,
            )
            now = datetime(2024, 6, 1, tzinfo=timezone.utc)
            trader.poll(now=now)  # processes all closed historical bars
            buys = [o for o in client.orders if o["side"] == "buy"]
            self.assertGreaterEqual(len(buys), 1)
            first = buys[0]
            self.assertIsNotNone(first.get("stop"))  # bracket stop attached for stocks
            self.assertGreater(first["qty"], 0)
            self.assertIsNotNone(trader.state["last_ts"])

    def test_crypto_stop_managed_locally(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_payload(), price=109.0)
            trader = LiveTrader(
                client, build("rsi_meanrev"), BotConfig(), "BTC/USD", "1d",
                state_path=tmp / "state.json", stream=io.StringIO(), clock=lambda s: None,
            )
            trader.state["stops"]["BTC/USD"] = 105.0
            # Latest bar low 105.0 <= stop -> close
            trader._check_crypto_stop(bars_from_payload(_payload()),
                                      datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertIn("BTC/USD", client.closed)
            self.assertNotIn("BTC/USD", trader.state["stops"])



    def test_capital_caps_position_notional(self) -> None:
        """A $1,000 wallet on a $100k paper account must never risk $100k."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_payload())
            bars = bars_from_payload(_payload())
            base = datetime(2024, 1, 5, tzinfo=timezone.utc)

            def price(k: int) -> float:
                return 104.0 - k if k < 60 else 44.0 + (k - 60)

            client.payload = {
                "bars": _payload()["bars"]
                + [
                    {
                        "t": (base + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
                        "o": price(k), "h": price(k) + 1.0, "l": price(k) - 1.0,
                        "c": price(k), "v": 1000.0,
                    }
                    for k in range(120)
                ]
            }
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": bars[0].ts.isoformat(), "stops": {}})
            )
            trader = LiveTrader(
                client, build("sma_cross"), BotConfig(), "SPY", "1d",
                state_path=tmp / "state.json", max_catchup=1000, capital=1_000.0,
                stream=io.StringIO(), clock=lambda s: None,
            )
            # account equity is 10,000 in _FakeClient.get_account()
            trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            buys = [o for o in client.orders if o["side"] == "buy"]
            self.assertGreaterEqual(len(buys), 1)
            entry = price(120 - 1)  # last processed bar's price order of magnitude
            self.assertLessEqual(buys[0]["qty"] * entry, 1_000.0 * 1.01)


class LiveAnchorTests(unittest.TestCase):
    """A fresh state file must not replay months of history, and a long gap
    must not be acted on as a backlog."""

    def _make(
        self, tmp: Path, payload: dict, *, max_catchup: int = 3
    ) -> tuple[LiveTrader, _FakeClient]:
        client = _FakeClient(payload)
        trader = LiveTrader(
            client,
            build("sma_cross"),
            BotConfig(initial_cash=10_000.0),
            "SPY",
            "1d",
            state_path=tmp / "state.json",
            poll=60,
            lookback=300,
            max_catchup=max_catchup,
            stream=io.StringIO(),
            clock=lambda s: None,
        )
        return trader, client

    @staticmethod
    def _long_payload(n: int = 60) -> dict:
        """Enough daily bars to satisfy sma_cross warmup (51)."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = [
            {
                "t": (start + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
                "o": 100.0 + k, "h": 101.0 + k, "l": 99.0 + k, "c": 100.5 + k, "v": 1000.0,
            }
            for k in range(n)
        ]
        return {"bars": rows, "next_page_token": None}

    def test_fresh_state_anchors_and_trades_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._make(tmp, self._long_payload())
            n = trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(n, 0)
            self.assertEqual(client.orders, [])
            state = json.loads((tmp / "state.json").read_text())
            # bar 58 is the newest closed bar (bar 59 is the still-forming one)
            self.assertEqual(state["last_ts"], "2024-02-28T00:00:00+00:00")
            self.assertIn("not trading", trader.stream.getvalue())

    def test_long_gap_resumes_on_the_newest_bars(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": "2024-01-01T00:00:00+00:00", "stops": {}})
            )
            trader, _ = self._make(tmp, self._long_payload(), max_catchup=1)
            n = trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(n, 1)  # only the newest bar, not the whole backlog
            self.assertIn("missed bars skipped", trader.stream.getvalue())


class FetchWindowTests(unittest.TestCase):
    """'Last N bars' must mean the newest N, not the oldest N after now-span."""

    def _client(self, pages: list[dict]) -> AlpacaClient:
        client = AlpacaClient("key", "secret")
        client._pages = pages  # type: ignore[attr-defined]
        client.params = []  # type: ignore[attr-defined]
        client._request = lambda url: (  # type: ignore[method-assign]
            client.params.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
            or client._pages.pop(0)
        )
        return client

    @staticmethod
    def _page(hours: list[int], token: str | None = None) -> dict:
        rows = [
            {
                "t": (datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=h)).isoformat(),
                "o": 100.0 + h, "h": 101.0 + h, "l": 99.0 + h, "c": 100.5 + h, "v": 10.0,
            }
            for h in hours
        ]
        return {"bars": {"BTC/USD": rows}, "next_page_token": token}

    def test_newest_bars_are_returned_and_trimmed(self) -> None:
        client = self._client([self._page([0, 4], "next"), self._page([8, 12])])
        bars = client.fetch_bars("BTC/USD", "4h", limit=3)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[-1].ts, datetime(2024, 1, 1, 12, tzinfo=timezone.utc))
        # no start given -> one is derived, and paging continues past the limit
        self.assertIn("start", client.params[0])
        self.assertIn("page_token", client.params[1])

    def test_explicit_start_stops_at_the_limit(self) -> None:
        client = self._client([self._page([0, 4], "next"), self._page([8, 12])])
        bars = client.fetch_bars(
            "BTC/USD", "4h", start=datetime(2024, 1, 1, tzinfo=timezone.utc), limit=2
        )
        self.assertEqual(len(bars), 2)
        self.assertEqual(len(client.params), 1)

if __name__ == "__main__":
    unittest.main()

class HeartbeatWiringTests(unittest.TestCase):
    """The loop must prove it is alive every cycle, not only when it trades."""

    def test_poll_pings_the_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader = LiveTrader(
                _FakeClient(_payload()),
                build("sma_cross"),
                BotConfig(initial_cash=10_000.0),
                "SPY",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=300,
                stream=io.StringIO(),
                clock=lambda s: None,
            )
            calls: list[str] = []
            trader.heartbeat = Heartbeat(
                "https://hc.example/abc", every=1, transport=lambda url, t: calls.append(url)
            )
            trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(calls, ["https://hc.example/abc"])

    def test_disabled_heartbeat_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader = LiveTrader(
                _FakeClient(_payload()),
                build("sma_cross"),
                BotConfig(initial_cash=10_000.0),
                "SPY",
                "1d",
                state_path=tmp / "state.json",
                stream=io.StringIO(),
                clock=lambda s: None,
            )
            self.assertFalse(trader.heartbeat.enabled)
            trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(trader.heartbeat.sent, 0)


if __name__ == "__main__":
    unittest.main()
