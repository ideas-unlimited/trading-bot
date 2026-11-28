"""Offline tests for the Binance live client helpers and the live loop."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from bot.config import BotConfig
from bot.data.feed import Bar
from bot.exchange.live_client import duration_for, interval_for, split_symbol
from bot.heartbeat import Heartbeat
from bot.live import LiveTrader, compute_entry_qty
from bot.strategy import build
from bot.strategy.base import Action, Signal


def _rows() -> list[dict]:
    """Four daily bars in the simple row shape the fake venue returns."""

    def row(day: int, o: float, h: float, l: float, c: float) -> dict:
        return {
            "t": f"2024-01-{day:02d}T00:00:00Z",
            "o": o, "h": h, "l": l, "c": c, "v": 1000.0,
        }

    return [
        row(1, 100, 105, 99, 104),
        row(2, 104, 106, 100, 103),
        row(3, 103, 107, 102, 106),
        row(4, 106, 110, 105, 109),
    ]


def _bars(rows: list[dict]) -> list[Bar]:
    return [
        Bar(
            ts=datetime.fromisoformat(r["t"].replace("Z", "+00:00")),
            open=float(r["o"]), high=float(r["h"]), low=float(r["l"]),
            close=float(r["c"]), volume=float(r.get("v") or 0.0),
        )
        for r in rows
    ]


class _FakeClient:
    """Scripted stand-in for BinanceLiveClient; never touches the network."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.orders: list[dict] = []
        self.closed: list[str] = []

    def fetch_bars(self, symbol, timeframe, limit=400, **kwargs):
        return _bars(self.rows)

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


class LiveClientHelpersTests(unittest.TestCase):
    def test_interval_aliases(self) -> None:
        self.assertEqual(interval_for("4h"), ("4h", 1))
        self.assertEqual(interval_for("1w"), ("1w", 1))
        self.assertEqual(interval_for("1Day"), ("1d", 1))
        with self.assertRaises(ValueError):
            interval_for("9h")

    def test_duration(self) -> None:
        self.assertEqual(duration_for("4h"), timedelta(hours=4))
        self.assertEqual(duration_for("1w"), timedelta(days=7))

    def test_symbol_split(self) -> None:
        self.assertEqual(split_symbol("BTC/USD"), ("BTC", "USDT", "BTCUSDT"))
        self.assertEqual(split_symbol("ethusdt"), ("ETH", "USDT", "ETHUSDT"))
        with self.assertRaises(ValueError):
            split_symbol("BTC")

    def test_rows_become_bars(self) -> None:
        bars = _bars(_rows())
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


class ClientBarsTests(unittest.TestCase):
    """fetch_bars asks production klines and aggregates locally."""

    def test_weekly_timeframe_uses_the_native_interval(self) -> None:
        import bot.exchange.live_client as lc

        start = datetime(2024, 1, 4, tzinfo=timezone.utc)
        hourly = [
            Bar(
                ts=start + timedelta(days=k), open=100.0 + k, high=101.0 + k,
                low=99.0 + k, close=100.5 + k, volume=10.0,
            )
            for k in range(7)
        ]
        calls: list[tuple] = []

        def fake(pair, interval, **kwargs):
            calls.append((pair, interval, kwargs))
            return hourly

        client = lc.BinanceLiveClient(api_key="k", api_secret="s")
        with mock.patch.object(lc, "fetch_binance_bars", side_effect=fake):
            bars = client.fetch_bars("ETH/USDT", "1w")
        self.assertEqual(calls[0][0], "ETHUSDT")
        self.assertEqual(calls[0][1], "1w")
        self.assertEqual(len(bars), 7)  # Binance has native weekly klines
        self.assertEqual(bars[-1].close, 106.5)


class LiveTraderTests(unittest.TestCase):
    def _make(self, tmp: Path, strategy_name: str = "sma_cross") -> tuple[LiveTrader, _FakeClient]:
        client = _FakeClient(_rows())
        config = BotConfig(initial_cash=10_000.0)
        trader = LiveTrader(
            client,
            build(strategy_name),
            config,
            "BTC/USDT",
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

    def test_buy_order_carries_local_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_rows())
            bars = _bars(_rows())
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
            client.rows = _rows() + extra
            # Seed the state so the loop treats the history as a backlog to
            # process (a fresh state now anchors instead of replaying).
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": bars[0].ts.isoformat(), "stops": {}})
            )
            trader = LiveTrader(
                client, build("sma_cross"), BotConfig(), "BTC/USDT", "1d",
                state_path=tmp / "state.json", max_catchup=1000,
                stream=io.StringIO(), clock=lambda s: None,
            )
            now = datetime(2024, 6, 1, tzinfo=timezone.utc)
            trader.poll(now=now)  # processes all closed historical bars
            buys = [o for o in client.orders if o["side"] == "buy"]
            self.assertGreaterEqual(len(buys), 1)
            first = buys[0]
            self.assertIsNotNone(first.get("stop"))  # spot stop is local, not a venue bracket
            self.assertIn("price", first)
            self.assertGreater(first["qty"], 0)
            self.assertIsNotNone(trader.state["last_ts"])

    def test_stop_managed_locally(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_rows())
            trader = LiveTrader(
                client, build("rsi_meanrev"), BotConfig(), "BTC/USDT", "1d",
                state_path=tmp / "state.json", stream=io.StringIO(), clock=lambda s: None,
            )
            trader.state["stops"]["BTC/USDT"] = 105.0
            # Latest bar low 105.0 <= stop -> close
            trader._check_stop(_bars(_rows()),
                                      datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertIn("BTC/USDT", client.closed)
            self.assertNotIn("BTC/USDT", trader.state["stops"])

    def test_capital_caps_position_notional(self) -> None:
        """A $1,000 wallet on a $10k demo account must never size $10k."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_rows())
            bars = _bars(_rows())
            base = datetime(2024, 1, 5, tzinfo=timezone.utc)

            def price(k: int) -> float:
                return 104.0 - k if k < 60 else 44.0 + (k - 60)

            client.rows = _rows() + [
                {
                    "t": (base + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
                    "o": price(k), "h": price(k) + 1.0, "l": price(k) - 1.0,
                    "c": price(k), "v": 1000.0,
                }
                for k in range(120)
            ]
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": bars[0].ts.isoformat(), "stops": {}})
            )
            trader = LiveTrader(
                client, build("sma_cross"), BotConfig(), "BTC/USDT", "1d",
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
        self, tmp: Path, rows: list[dict], *, max_catchup: int = 3
    ) -> tuple[LiveTrader, _FakeClient]:
        client = _FakeClient(rows)
        trader = LiveTrader(
            client,
            build("sma_cross"),
            BotConfig(initial_cash=10_000.0),
            "BTC/USDT",
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
    def _long_rows(n: int = 60) -> list[dict]:
        """Enough daily bars to satisfy sma_cross warmup (51)."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            {
                "t": (start + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
                "o": 100.0 + k, "h": 101.0 + k, "l": 99.0 + k, "c": 100.5 + k, "v": 1000.0,
            }
            for k in range(n)
        ]

    def test_fresh_state_anchors_and_trades_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._make(tmp, self._long_rows())
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
            trader, _ = self._make(tmp, self._long_rows(), max_catchup=1)
            n = trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(n, 1)  # only the newest bar, not the whole backlog
            self.assertIn("missed bars skipped", trader.stream.getvalue())


class HeartbeatWiringTests(unittest.TestCase):
    """The loop must prove it is alive every cycle, not only when it trades."""

    def test_poll_pings_the_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader = LiveTrader(
                _FakeClient(_rows()),
                build("sma_cross"),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
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
                _FakeClient(_rows()),
                build("sma_cross"),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
                "1d",
                state_path=tmp / "state.json",
                stream=io.StringIO(),
                clock=lambda s: None,
            )
            self.assertFalse(trader.heartbeat.enabled)
            trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(trader.heartbeat.sent, 0)

class _BuyEveryBar:
    """Stub strategy: a buy on every bar, with a wide protective stop."""

    name = "test_always_buy"
    warmup = 1
    params: dict = {}

    def prepare(self, df):
        return df

    def signal(self, i, prepared, position):
        close = float(prepared["close"].iloc[i])
        return Signal(
            Action.BUY, reason="test", stop_loss=close * 0.5, take_profit=close * 1.5
        )


class KillSwitchAndHistoryTests(unittest.TestCase):
    """The bot page answers "in a trade? where is the stop? what filled?"
    from the loop, so these book-keeping paths must be tested like orders."""

    def _trader(self, tmp: Path, strategy=None, **kwargs) -> tuple[LiveTrader, _FakeClient]:
        client = _FakeClient(_rows())
        trader = LiveTrader(
            client, strategy or build("sma_cross"), BotConfig(), "BTC/USDT", "1d",
            state_path=tmp / "state.json", stream=io.StringIO(),
            clock=lambda s: None, lookback=300, **kwargs,
        )
        return trader, client

    def test_buy_books_a_fill_and_its_protective_levels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "state.json").write_text(
                json.dumps({"last_ts": _bars(_rows())[0].ts.isoformat(), "stops": {}})
            )
            trader, client = self._trader(tmp, _BuyEveryBar(), max_catchup=100)
            fills: list[tuple] = []
            trader._book_fill = lambda side, qty, price, **kw: fills.append((side, qty, price, kw))
            trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            self.assertEqual(fills[0][0], "buy")
            self.assertGreater(fills[0][1], 0)
            entry = trader.state["entries"]["BTC/USDT"]
            self.assertAlmostEqual(entry["price"], fills[0][2])
            # The stop lives in the state file; the target is remembered too,
            # otherwise the page cannot show what the trade is aiming at.
            self.assertIn("BTC/USDT", trader.state["stops"])
            self.assertIn("BTC/USDT", trader.state["tps"])

    def test_kill_switch_flattens_and_blocks_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._trader(tmp, _BuyEveryBar())
            client.orders.append({"symbol": "BTC/USDT", "side": "buy", "qty": 0.5})
            trader.state["entries"]["BTC/USDT"] = {
                "qty": 0.5, "price": 100.0, "ts": "2024-01-03T00:00:00+00:00",
            }
            trader.state["stops"]["BTC/USDT"] = 95.0
            trader._halted = lambda: True  # the dashboard pressed kill
            fills: list[tuple] = []
            trader._book_fill = lambda side, qty, price, **kw: fills.append((side, qty, price, kw))
            n = trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            self.assertEqual(n, 0)
            self.assertIn("BTC/USDT", client.closed)
            # No new entries while halted, and no stale stop left behind.
            self.assertEqual(len([o for o in client.orders if o["side"] == "buy"]), 1)
            self.assertNotIn("BTC/USDT", trader.state["stops"])
            self.assertEqual(fills[0][0], "sell")
            self.assertEqual(fills[0][3].get("closed_by"), "halt")
            self.assertAlmostEqual(fills[0][3]["realized"], (109.0 - 100.0) * 0.5)
            self.assertIn("HALTED", trader.stream.getvalue())

    def test_view_exposes_position_stop_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trader, _ = self._trader(Path(td))
            trader.state["entries"]["BTC/USDT"] = {"qty": 0.5, "price": 100.0, "ts": "x"}
            trader.state["stops"]["BTC/USDT"] = 95.0
            trader.state["tps"]["BTC/USDT"] = 120.0
            trader._view(0.5, _bars(_rows()))
            view = trader._last_view
            self.assertEqual(view["position_qty"], 0.5)
            self.assertEqual(view["entry_price"], 100.0)
            self.assertEqual(view["stop"], 95.0)
            self.assertEqual(view["take_profit"], 120.0)
            self.assertEqual(view["mark_price"], 109.0)



if __name__ == "__main__":
    unittest.main()
