"""Offline tests for the Binance live client helpers and the live loop."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from bot.backtest import bars_to_frame
from bot.config import BotConfig, RiskConfig
from bot.data.feed import Bar
from bot.exchange.order_client import duration_for, interval_for, split_symbol
from bot.heartbeat import Heartbeat
from bot.deploy import DeployTrader, compute_entry_qty
from bot.strategy import build
from bot.strategy.base import Action, Signal, Strategy


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
    """Scripted stand-in for BinanceOrderClient; never touches the network."""

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


class _AccountClient(_FakeClient):
    """Fake venue whose equity can fall, for the drawdown-brake path."""

    def __init__(self, rows: list[dict], equity: float):
        super().__init__(rows)
        self.equity = equity

    def get_account(self):
        return {"equity": self.equity, "cash": self.equity}


class _TrackingClient(_FakeClient):
    """Fake venue that actually goes flat when a position is closed."""

    def __init__(self, rows: list[dict]):
        super().__init__(rows)
        self.qty = 0.0

    def submit_order(self, symbol, side, qty, **kwargs):
        order = super().submit_order(symbol, side, qty, **kwargs)
        if side == "buy":
            self.qty += float(qty)
        else:
            self.qty = max(0.0, self.qty - float(qty))
        return order

    def close_position(self, symbol):
        self.qty = 0.0
        return super().close_position(symbol)

    def get_position(self, symbol):
        return {"qty": self.qty} if self.qty else None


class _DustClient(_FakeClient):
    """Fake venue that reports an unsellable leftover as a balance."""

    def __init__(self, rows: list[dict], qty: float):
        super().__init__(rows)
        self.qty = qty

    def get_position(self, symbol):
        return {"qty": self.qty} if self.qty else None

    def filters(self, symbol):
        from bot.exchange import SymbolFilters

        return SymbolFilters(
            symbol="JUPUSDT", step_size=0.1, tick_size=0.00001, min_qty=0.1
        )


class TrailBuy(Strategy):
    """Enters once, then lets the trail be the only exit."""

    name = "test_trail_buy"
    warmup = 1
    PARAMS = ("trail", "after", "arm")

    def __init__(
        self,
        trail: float | None = 2.0,
        after: float = 0.0,
        arm: float | None = None,
    ) -> None:
        super().__init__(trail=trail, after=after, arm=arm)

    def prepare(self, df):
        out = df.copy()
        out["atr"] = 2.0
        return out

    def signal(self, i, df, position):
        if position <= 0 and i == 0:
            return Signal(
                Action.BUY, "test trail", stop_loss=90.0,
                trail_atr=self.trail, trail_after_r=self.after,
                trail_after_price=self.arm,
            )
        return None


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
        import bot.exchange.order_client as lc

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

        client = lc.BinanceOrderClient(api_key="k", api_secret="s")
        with mock.patch.object(lc, "fetch_binance_bars", side_effect=fake):
            bars = client.fetch_bars("ETH/USDT", "1w")
        self.assertEqual(calls[0][0], "ETHUSDT")
        self.assertEqual(calls[0][1], "1w")
        self.assertEqual(len(bars), 7)  # Binance has native weekly klines
        self.assertEqual(bars[-1].close, 106.5)


class BuyNow(Strategy):
    name = "test_buy_now"
    warmup = 1
    PARAMS = ()

    def prepare(self, df):
        return df

    def signal(self, i, df, position):
        return Signal(Action.BUY, "test entry", stop_loss=90.0, take_profit=120.0)


class Capture:
    enabled = True

    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def send_trade(self, event):
        self.calls.append(dict(event))
        return self.ok


class DeployTraderTests(unittest.TestCase):
    def _make(self, tmp: Path, strategy_name: str = "sma_cross") -> tuple[DeployTrader, _FakeClient]:
        client = _FakeClient(_rows())
        config = BotConfig(initial_cash=10_000.0)
        trader = DeployTrader(
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

    def test_a_leftover_below_the_lot_step_reads_as_flat(self) -> None:
        """Dust must not count as a position: nothing could ever sell it."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _DustClient(_rows(), 0.0949)
            trader = DeployTrader(
                client,
                build("sma_cross"),
                BotConfig(initial_cash=10_000.0),
                "JUP/USDT",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=300,
                stream=io.StringIO(),
                clock=lambda s: None,
            )
            self.assertEqual(trader._position_qty(), 0.0)
            self.assertIn("below the lot step", trader.stream.getvalue())
            # Logged once, not once a minute for the life of the bot.
            trader._position_qty()
            self.assertEqual(trader.stream.getvalue().count("below the lot step"), 1)
            client.qty = 1.5
            self.assertEqual(trader._position_qty(), 1.5)

    def test_drawdown_brake_blocks_entries_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _AccountClient(_rows(), equity=850.0)
            (tmp / "state.json").write_text(
                json.dumps(
                    {
                        "last_ts": "2024-01-01T00:00:00+00:00",
                        "stops": {},
                        "risk": {
                            "peak_equity": 1_000.0,
                            "halted": False,
                            "halt_reason": "",
                        },
                    }
                )
            )
            config = BotConfig(
                initial_cash=1_000.0,
                risk=RiskConfig(max_drawdown_stop=0.10),
            )
            trader = DeployTrader(
                client, BuyNow(), config, "BTC/USDT", "1d",
                state_path=tmp / "state.json",
                stream=io.StringIO(), clock=lambda s: None,
            )
            trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(client.orders, [])
            self.assertTrue(trader.risk.halted)
            self.assertTrue(trader.state["risk"]["halted"])

            again = DeployTrader(
                client, BuyNow(), config, "BTC/USDT", "1d",
                state_path=tmp / "state.json",
                stream=io.StringIO(), clock=lambda s: None,
            )
            self.assertTrue(again.risk.halted)
            self.assertEqual(again.risk.peak_equity, 1_000.0)

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
            trader = DeployTrader(
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
            trader = DeployTrader(
                client, build("rsi_meanrev"), BotConfig(), "BTC/USDT", "1d",
                state_path=tmp / "state.json", stream=io.StringIO(), clock=lambda s: None,
            )
            trader.state["stops"]["BTC/USDT"] = 105.0
            # Latest bar low 105.0 <= stop -> close
            trader._check_exits(_bars(_rows()),
                                      datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertIn("BTC/USDT", client.closed)
            self.assertNotIn("BTC/USDT", trader.state["stops"])

    def test_target_managed_locally(self) -> None:
        """A fixed-R target must close the position, not wait for the stop."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_rows())
            trader = DeployTrader(
                client, build("rsi_meanrev"), BotConfig(), "BTC/USDT", "1d",
                state_path=tmp / "state.json", stream=io.StringIO(), clock=lambda s: None,
            )
            trader.state["stops"]["BTC/USDT"] = 80.0
            trader.state["tps"]["BTC/USDT"] = 108.0   # latest bar high is 110
            trader._check_exits(_bars(_rows()), datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertIn("BTC/USDT", client.closed)
            self.assertNotIn("BTC/USDT", trader.state["tps"])
            self.assertNotIn("BTC/USDT", trader.state["stops"])

    def test_stop_wins_when_both_are_touched(self) -> None:
        """Same bar through stop and target is a loss, like the engine."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(_rows())
            trader = DeployTrader(
                client, build("rsi_meanrev"), BotConfig(), "BTC/USDT", "1d",
                state_path=tmp / "state.json", stream=io.StringIO(), clock=lambda s: None,
            )
            trader.state["stops"]["BTC/USDT"] = 106.0   # latest low is 105
            trader.state["tps"]["BTC/USDT"] = 108.0     # latest high is 110
            trader._check_exits(_bars(_rows()), datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertIn("BTC/USDT", client.closed)
            self.assertNotIn("BTC/USDT", trader.state["tps"])

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
            trader = DeployTrader(
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


class LiveTrailTests(unittest.TestCase):
    """Chandelier trail in the live loop, with the engine's ordering."""

    def _trader(self, tmp: Path, client: _TrackingClient, strategy=None) -> DeployTrader:
        return DeployTrader(
            client, strategy or TrailBuy(), BotConfig(initial_cash=10_000.0),
            "BTC/USDT", "1d", state_path=tmp / "state.json",
            stream=io.StringIO(), clock=lambda s: None,
        )

    def _rows(self) -> list[dict]:
        def row(day: int, o: float, h: float, l: float, c: float) -> dict:
            return {
                "t": f"2024-01-{day:02d}T00:00:00Z",
                "o": o, "h": h, "l": l, "c": c, "v": 1000.0,
            }

        return [
            row(1, 100, 200, 99, 100),   # signal bar: huge high must never trail
            row(2, 100, 120, 100, 118),  # peak 120 -> 2 ATR trail at 116
            row(3, 118, 119, 115, 117),  # old stop 116 fills before any new trail
            row(4, 117, 121, 116, 120),
        ]

    def _seed(self, trader: DeployTrader, bars: list[Bar]) -> None:
        trader.state["entries"]["BTC/USDT"] = {
            "qty": 1.0, "price": 100.0, "ts": bars[0].ts.isoformat(),
        }
        trader.state["stops"]["BTC/USDT"] = 90.0
        trader._set_trail(
            Signal(Action.BUY, "trail", stop_loss=90.0, trail_atr=2.0),
            100.0, bars[0].ts,
        )

    def test_entry_bar_is_never_trailed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            self._seed(trader, bars)
            trader._ratchet_trail(bars[0], prepared, 0)
            self.assertEqual(trader.state["stops"]["BTC/USDT"], 90.0)
            trader._ratchet_trail(bars[1], prepared, 1)
            self.assertAlmostEqual(trader.state["stops"]["BTC/USDT"], 116.0)

    def test_entry_bar_cannot_stop_a_position_opened_at_its_close(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            self._seed(trader, bars)
            entry_bar = Bar(
                ts=bars[0].ts, open=100.0, high=101.0, low=80.0,
                close=100.0, volume=1.0,
            )
            trader._check_exits_bar(entry_bar)
            self.assertEqual(client.closed, [])

    def test_trail_never_moves_down(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            self._seed(trader, bars)
            trader._ratchet_trail(bars[1], prepared, 1)
            self.assertAlmostEqual(trader.state["stops"]["BTC/USDT"], 116.0)
            trader._ratchet_trail(bars[2], prepared, 2)  # high 119 < old peak
            self.assertAlmostEqual(trader.state["stops"]["BTC/USDT"], 116.0)
            self.assertAlmostEqual(trader.state["trails"]["BTC/USDT"]["peak"], 120.0)

    def test_exit_check_runs_before_the_trail_raises(self) -> None:
        """A bar may not both lift the stop and fill against the lifted stop."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            self._seed(trader, bars)
            trader.state["stops"]["BTC/USDT"] = 100.0
            bar = Bar(
                ts=bars[1].ts, open=107.0, high=110.0, low=105.0,
                close=107.0, volume=1.0,
            )
            trader._check_exits_bar(bar)  # low 105 is above the old stop 100
            self.assertEqual(client.closed, [])
            trader._ratchet_trail(bar, prepared, 1)  # then lifts to 106
            self.assertAlmostEqual(trader.state["stops"]["BTC/USDT"], 106.0)

    def test_signal_without_a_trail_keeps_the_fixed_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            trader._set_trail(
                Signal(Action.BUY, "fixed", stop_loss=90.0, trail_atr=None),
                100.0, bars[0].ts,
            )
            trader.state["stops"]["BTC/USDT"] = 90.0
            self.assertNotIn("BTC/USDT", trader.state["trails"])
            trader._ratchet_trail(bars[1], prepared, 1)
            self.assertEqual(trader.state["stops"]["BTC/USDT"], 90.0)

    def test_trail_after_r_waits_for_the_configured_gain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            trader._set_trail(
                Signal(Action.BUY, "trail", stop_loss=90.0, trail_atr=2.0,
                       trail_after_r=1.0),
                100.0, bars[0].ts,
            )
            trader.state["stops"]["BTC/USDT"] = 90.0
            early = Bar(
                ts=bars[1].ts, open=104.0, high=105.0, low=104.0,
                close=105.0, volume=1.0,
            )
            trader._ratchet_trail(early, prepared, 1)  # 0.5R: not armed
            self.assertEqual(trader.state["stops"]["BTC/USDT"], 90.0)
            late = Bar(
                ts=bars[2].ts, open=105.0, high=111.0, low=105.0,
                close=111.0, volume=1.0,
            )
            trader._ratchet_trail(late, prepared, 2)  # 1.1R: peak 111 -> 107
            self.assertAlmostEqual(trader.state["stops"]["BTC/USDT"], 107.0)

    def test_trail_can_wait_for_a_price_level(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            trader._set_trail(
                Signal(Action.BUY, "trail", stop_loss=90.0, trail_atr=2.0,
                       trail_after_price=130.0),
                100.0, bars[0].ts,
            )
            trader.state["stops"]["BTC/USDT"] = 90.0
            trader._ratchet_trail(bars[1], prepared, 1)  # high 120 < 130
            self.assertEqual(trader.state["stops"]["BTC/USDT"], 90.0)
            self.assertEqual(trader.state["trails"]["BTC/USDT"]["after_price"], 130.0)

    def test_trail_state_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            self._seed(trader, bars)
            trader._ratchet_trail(bars[1], prepared, 1)
            trader._save_state()
            restarted = self._trader(tmp, client)
            lower = _bars([
                {"t": "2024-01-03T00:00:00Z", "o": 118, "h": 118,
                 "l": 117, "c": 117, "v": 1000.0},
            ])[0]
            restarted._ratchet_trail(lower, prepared, 2)
            self.assertAlmostEqual(restarted.state["stops"]["BTC/USDT"], 116.0)
            self.assertAlmostEqual(restarted.state["trails"]["BTC/USDT"]["peak"], 120.0)

    def test_poll_trails_then_stops_on_the_middle_bar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            trader = self._trader(tmp, client)
            # Pretend the previous poll stopped before the first test bar,
            # so this poll processes the entry, the trail and the stop.
            trader.state["last_ts"] = "2023-12-31T00:00:00+00:00"
            trader.poll(now=datetime(2024, 1, 5, tzinfo=timezone.utc))
            self.assertEqual(client.closed, ["BTC/USDT"])
            self.assertNotIn("BTC/USDT", trader.state["stops"])
            self.assertNotIn("BTC/USDT", trader.state["trails"])
            log = trader.stream.getvalue()
            self.assertIn("TRAIL stop=116.0000", log)
            self.assertIn("STOP hit", log)

    def test_stop_gap_fills_at_the_open(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = self._rows()
            client = _TrackingClient(rows)
            client.qty = 1.0
            trader = self._trader(tmp, client)
            trader.state["stops"]["BTC/USDT"] = 100.0
            gap = Bar(
                ts=datetime(2024, 1, 3, tzinfo=timezone.utc),
                open=90.0, high=95.0, low=85.0, close=92.0, volume=1.0,
            )
            with mock.patch.object(trader, "_exit") as exit_mock:
                trader._check_exits_bar(gap)
            exit_mock.assert_called_once()
            self.assertEqual(exit_mock.call_args.args[1], 90.0)


class LiveAlertTests(unittest.TestCase):
    def _trader(self, tmp: Path, notifier) -> tuple[DeployTrader, _FakeClient]:
        client = _FakeClient(_rows())
        trader = DeployTrader(
            client, BuyNow(), BotConfig(), "BTC/USDT", "1d",
            state_path=tmp / "state.json", stream=io.StringIO(),
            clock=lambda s: None, notifier=notifier,
        )
        return trader, client

    def test_buy_alert_carries_stop_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            capture = Capture()
            trader, client = self._trader(tmp, capture)
            bars = _bars(_rows())
            trader._process_bar(
                bars[-1], bars, 10_000.0, 0.0,
                datetime(2024, 1, 5, tzinfo=timezone.utc),
            )
            self.assertEqual(len(client.orders), 1)
            self.assertEqual(len(capture.calls), 1)
            event = capture.calls[0]
            self.assertEqual(event["side"], "buy")
            self.assertEqual(event["entry"], bars[-1].close)
            self.assertEqual(event["stop"], 90.0)
            self.assertEqual(event["take_profit"], 120.0)
            self.assertEqual(event["ts"], bars[-1].ts)
            self.assertGreater(event["qty"], 0)

    def test_exit_alert_reports_pnl_and_clears_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            capture = Capture()
            trader, client = self._trader(tmp, capture)
            opened = datetime(2024, 1, 1, tzinfo=timezone.utc)
            trader.state["entries"]["BTC/USDT"] = {
                "qty": 2.0, "price": 100.0, "ts": opened.isoformat(),
            }
            trader.state["stops"]["BTC/USDT"] = 90.0
            trader.state["tps"]["BTC/USDT"] = 120.0
            trader._exit(
                2.0, 110.0, "take profit", opened + timedelta(days=1),
                equity=1_020.0,
            )
            self.assertEqual(client.closed, ["BTC/USDT"])
            self.assertNotIn("BTC/USDT", trader.state["entries"])
            self.assertEqual(len(capture.calls), 1)
            event = capture.calls[0]
            self.assertEqual(event["side"], "sell")
            self.assertEqual(event["entry"], 100.0)
            self.assertEqual(event["stop"], 90.0)
            self.assertEqual(event["take_profit"], 120.0)
            self.assertEqual(event["pnl"], 20.0)
            self.assertAlmostEqual(event["return_pct"], 0.10)
            self.assertEqual(event["held_hours"], 24.0)
            self.assertEqual(event["reason"], "take profit")
            self.assertEqual(event["wallet"], 1_020.0)

    def test_alert_failure_does_not_block_the_exit(self) -> None:
        class Broken:
            enabled = True

            def send_trade(self, event):
                raise RuntimeError("mail down")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._trader(tmp, Broken())
            trader.state["entries"]["BTC/USDT"] = {
                "qty": 1.0, "price": 100.0, "ts": "2024-01-01T00:00:00+00:00",
            }
            trader._exit(1.0, 100.0, "signal", datetime(2024, 1, 2, tzinfo=timezone.utc))
            self.assertEqual(client.closed, ["BTC/USDT"])
            self.assertIn("trade alerts failed", trader.stream.getvalue())


class LiveAnchorTests(unittest.TestCase):
    """A fresh state file must not replay months of history, and a long gap
    must not be acted on as a backlog."""

    def _make(
        self, tmp: Path, rows: list[dict], *, max_catchup: int = 3
    ) -> tuple[DeployTrader, _FakeClient]:
        client = _FakeClient(rows)
        trader = DeployTrader(
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

    def test_startup_replay_bootstraps_an_open_position(self) -> None:
        class BuyNoTarget(BuyNow):
            def signal(self, i, df, position):
                return Signal(Action.BUY, "test entry", stop_loss=90.0)

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(self._long_rows())
            trader = DeployTrader(
                client,
                BuyNoTarget(),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=300,
                stream=io.StringIO(),
                clock=lambda s: None,
                startup_replay=True,
            )
            trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            # The replay found the model already long; one new entry, never a
            # copied historical fill.
            self.assertEqual(len(client.orders), 1)
            self.assertEqual(client.orders[0]["side"], "buy")
            state = json.loads((tmp / "state.json").read_text())
            self.assertEqual(state["last_ts"], "2024-02-28T00:00:00+00:00")
            self.assertIn("startup replay", trader.stream.getvalue())
            self.assertIn("open position", trader.stream.getvalue())
            self.assertIn("historical fills/P&L not credited", trader.stream.getvalue())
            entry = state["entries"]["BTC/USDT"]
            self.assertAlmostEqual(entry["price"], 158.5)
            self.assertEqual(state["stops"]["BTC/USDT"], 90.0)

    def test_startup_replay_catches_a_pending_entry(self) -> None:
        class LastBarBuy(BuyNow):
            def signal(self, i, df, position):
                if position <= 0 and i == len(df) - 1:
                    return Signal(Action.BUY, "last bar", stop_loss=90.0)
                return None

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(self._long_rows())
            trader = DeployTrader(
                client,
                LastBarBuy(),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=300,
                stream=io.StringIO(),
                clock=lambda s: None,
                startup_replay=True,
            )
            trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(len(client.orders), 1)
            self.assertIn("pending entry", trader.stream.getvalue())

    def test_live_refetches_when_warmup_needs_more_bars(self) -> None:
        class SlicingClient(_FakeClient):
            def fetch_bars(self, symbol, timeframe, limit=400, **kwargs):
                return _bars(self.rows[-limit:])

        class NeedsWarmup(BuyNow):
            def prepare(self, df):
                self.warmup = 800
                return df

            def signal(self, i, df, position):
                return Signal(Action.BUY, "test entry", stop_loss=90.0)

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = SlicingClient(self._long_rows(900))
            trader = DeployTrader(
                client,
                NeedsWarmup(),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=40,
                stream=io.StringIO(),
                clock=lambda s: None,
                startup_replay=True,
            )
            trader.poll(now=datetime(2026, 6, 19, tzinfo=timezone.utc))
            self.assertIn("refetching 802", trader.stream.getvalue())
            self.assertEqual(len(client.orders), 1)

    def test_startup_replay_stays_flat_without_a_signal(self) -> None:
        class NoSignal(BuyNow):
            def signal(self, i, df, position):
                return None

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client = _FakeClient(self._long_rows())
            trader = DeployTrader(
                client,
                NoSignal(),
                BotConfig(initial_cash=10_000.0),
                "BTC/USDT",
                "1d",
                state_path=tmp / "state.json",
                poll=60,
                lookback=300,
                stream=io.StringIO(),
                clock=lambda s: None,
                startup_replay=True,
            )
            trader.poll(now=datetime(2024, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(client.orders, [])
            state = json.loads((tmp / "state.json").read_text())
            self.assertEqual(state["last_ts"], "2024-02-28T00:00:00+00:00")
            self.assertIn("model flat", trader.stream.getvalue())

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
            trader = DeployTrader(
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
            trader = DeployTrader(
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

    def _trader(self, tmp: Path, strategy=None, **kwargs) -> tuple[DeployTrader, _FakeClient]:
        client = _FakeClient(_rows())
        trader = DeployTrader(
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



class EventLogTests(unittest.TestCase):
    """D8: signals, fills, rejects, halts and errors reach the events table,
    and 'no signal' polls never write a row (the table is a logbook, not a
    heartbeat)."""

    def _trader(self, tmp: Path, strategy=None, **kwargs) -> tuple[DeployTrader, _FakeClient]:
        client = _FakeClient(_rows())
        trader = DeployTrader(
            client, strategy or build("sma_cross"), BotConfig(), "BTC/USDT", "1d",
            state_path=tmp / "state.json", stream=io.StringIO(),
            clock=lambda s: None, lookback=300, **kwargs,
        )
        return trader, client

    @staticmethod
    def _capture(trader: DeployTrader) -> list[tuple]:
        events: list[tuple] = []
        trader._event = (  # type: ignore[method-assign]
            lambda kind, message="", *, level="info", payload=None:
            events.append((kind, message, level, payload))
        )
        return events

    def _buy_state(self, tmp: Path) -> None:
        (tmp / "state.json").write_text(
            json.dumps({"last_ts": _bars(_rows())[0].ts.isoformat(), "stops": {}})
        )

    def test_buy_fill_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._buy_state(tmp)
            trader, _ = self._trader(tmp, _BuyEveryBar(), max_catchup=100)
            events = self._capture(trader)
            trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            fills = [e for e in events if e[0] == "fill" and "BUY" in e[1]]
            self.assertEqual(len(fills), 1)
            payload = fills[0][3]
            self.assertEqual(payload["side"], "buy")
            self.assertIn("stop", payload)
            self.assertIn("order_id", payload)

    def test_quiet_polls_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trader, _ = self._trader(Path(td))
            events = self._capture(trader)
            n = trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            # Fresh state anchors, bars 2-3 hold, bar 4 buys: stub the same
            # anchor path but assert the fresh anchor poll itself is silent.
            self.assertEqual(n, 0)
            self.assertEqual(events, [])

    def test_kill_switch_halt_logged_once_and_release_logged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trader, client = self._trader(tmp, _BuyEveryBar())
            trader.state["entries"]["BTC/USDT"] = {
                "qty": 0.5, "price": 100.0, "ts": "2024-01-03T00:00:00+00:00",
            }
            halted = {"on": True}
            trader._halted = lambda: halted["on"]
            trader.bot_id = 7
            events = self._capture(trader)
            trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            halts = [e for e in events if e[0] == "halt" and "engaged" in e[1]]
            self.assertEqual(len(halts), 1, "one halt row, not one per poll")
            # Released: the next poll logs the release exactly once.
            halted["on"] = False
            trader.state["last_ts"] = None  # anchor again, nothing trades
            trader.poll(now=datetime(2024, 1, 9, tzinfo=timezone.utc))
            self.assertEqual(len([e for e in events if e[0] == "halt"]), 2)

    def test_order_rejection_becomes_an_error_event_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._buy_state(tmp)
            trader, client = self._trader(tmp, _BuyEveryBar(), max_catchup=100)
            events = self._capture(trader)

            def boom(*args, **kwargs):
                raise RuntimeError("insufficient balance")

            client.submit_order = boom  # type: ignore[method-assign]
            with self.assertRaises(RuntimeError):
                trader.poll(now=datetime(2024, 1, 8, tzinfo=timezone.utc))
            errors = [e for e in events if e[0] == "error"]
            self.assertEqual(len(errors), 1)
            self.assertIn("insufficient balance", errors[0][1])
            self.assertEqual(errors[0][2], "error")

    def test_trail_ratchet_is_recorded(self) -> None:
        from bot.backtest import bars_to_frame

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = [
                {"t": f"2024-01-{d:02d}T00:00:00Z", "o": o, "h": h, "l": lo, "c": c, "v": 1000.0}
                for d, (o, h, lo, c) in enumerate(
                    [(100, 200, 99, 100), (100, 120, 100, 118)], start=1
                )
            ]
            client = _FakeClient(rows)
            client.orders.append({"symbol": "BTC/USDT", "side": "buy", "qty": 1.0})
            trader = DeployTrader(
                client, TrailBuy(), BotConfig(initial_cash=10_000.0),
                "BTC/USDT", "1d", state_path=tmp / "state.json",
                stream=io.StringIO(), clock=lambda s: None,
            )
            bars = _bars(rows)
            prepared = trader.strategy.prepare(bars_to_frame(bars))
            trader.state["entries"]["BTC/USDT"] = {
                "qty": 1.0, "price": 100.0, "ts": bars[0].ts.isoformat(),
            }
            trader.state["stops"]["BTC/USDT"] = 90.0
            trader._set_trail(
                Signal(Action.BUY, "trail", stop_loss=90.0, trail_atr=2.0),
                100.0, bars[0].ts,
            )
            events = self._capture(trader)
            trader._ratchet_trail(bars[1], prepared, 1)
            stops = [e for e in events if e[0] == "stop"]
            self.assertEqual(len(stops), 1)
            self.assertIn("trail raised", stops[0][1])
            self.assertEqual(stops[0][3]["stop"], 116.0)

    def test_event_helper_survives_a_dead_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trader, _ = self._trader(Path(td), record=True)
            trader.bot_id = 1

            class Boom:
                def connect(self):
                    raise RuntimeError("no postgres")

            import bot.db as store

            orig = store.connect
            store.connect = Boom  # type: ignore[assignment]
            try:
                trader._event("fill", "should not raise")
                trader._event("fill", "second call warns only once")
            finally:
                store.connect = orig  # type: ignore[assignment]
            self.assertIn("event log off", trader.stream.getvalue())
            self.assertEqual(trader.stream.getvalue().count("event log off"), 1)

    def test_event_helper_is_a_noop_without_registration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trader, _ = self._trader(Path(td))  # record=False (lab/tests default)
            # Neither the guard nor a broken store may raise here.
            trader._event("fill", "unregistered bot writes nothing")


if __name__ == "__main__":
    unittest.main()
