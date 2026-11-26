"""Tests for the TradingView alert parser and the simulation-money mirror."""

import unittest
from datetime import datetime, timezone

from bot.alerts import Alert, AlertError, MirrorAccount, parse_alert


def _alert(action: str = "buy", price: float = 100.0, stop: float | None = 95.0) -> Alert:
    return Alert(action=action, symbol="TEST", price=price, stop=stop)


class ParseAlertTests(unittest.TestCase):
    def test_pine_payload(self) -> None:
        body = '{"action":"buy","symbol":"BTCUSDT","price":50000.5,"stop":49000.0,"secret":"tok"}'
        alert = parse_alert(body)
        self.assertEqual(alert.action, "buy")
        self.assertEqual(alert.symbol, "BTCUSDT")
        self.assertEqual(alert.price, 50000.5)
        self.assertEqual(alert.stop, 49000.0)

    def test_aliases(self) -> None:
        self.assertEqual(parse_alert({"side": "long", "price": 10}).action, "buy")
        self.assertEqual(parse_alert({"action": "sell", "close": 10}).action, "close")
        self.assertEqual(parse_alert({"action": "exit", "price": 10}).action, "close")

    def test_errors(self) -> None:
        with self.assertRaises(AlertError):
            parse_alert("")
        with self.assertRaises(AlertError):
            parse_alert("not json")
        with self.assertRaises(AlertError):
            parse_alert({"action": "teleport", "price": 10})
        with self.assertRaises(AlertError):
            parse_alert({"action": "buy"})  # missing price
        with self.assertRaises(AlertError):
            parse_alert({"action": "buy", "price": -5})


class MirrorAccountTests(unittest.TestCase):
    def test_stop_sized_buy_and_close(self) -> None:
        acct = MirrorAccount(initial_cash=10_000.0, risk_per_trade=0.01)
        fill = acct.apply(_alert("buy", 100.0, 95.0), ts=datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert fill is not None
        self.assertEqual(fill.qty, 20.0)  # 1% of 10k risked over a 5-point stop
        self.assertEqual(fill.cash, 8_000.0)

        close = acct.apply(_alert("close", 102.0), ts=datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert close is not None
        self.assertEqual(close.qty, -20.0)
        self.assertEqual(acct.cash, 10_040.0)  # 8000 + 20*102
        self.assertEqual(acct.equity(102.0), 10_040.0)

    def test_capped_when_no_stop(self) -> None:
        acct = MirrorAccount(initial_cash=10_000.0, max_position_pct=0.5)
        fill = acct.apply(_alert("buy", 100.0, stop=None))
        assert fill is not None
        self.assertEqual(fill.qty, 50.0)  # 50% of equity / 100
        self.assertEqual(acct.cash, 5_000.0)

    def test_duplicate_buy_ignored(self) -> None:
        acct = MirrorAccount()
        first = acct.apply(_alert("buy", 100.0, 95.0))
        second = acct.apply(_alert("buy", 100.0, 95.0))
        assert first is not None and second is not None
        self.assertGreater(first.qty, 0)
        self.assertEqual(second.qty, 0.0)
        self.assertIn("ignored", second.note)

    def test_close_when_flat_ignored(self) -> None:
        acct = MirrorAccount()
        fill = acct.apply(_alert("close", 100.0))
        assert fill is not None
        self.assertEqual(fill.qty, 0.0)

    def test_drawdown_halt_blocks_new_buys(self) -> None:
        acct = MirrorAccount(initial_cash=10_000.0, max_drawdown_stop=0.25, max_position_pct=1.0)
        entry = acct.apply(_alert("buy", 100.0, stop=None))
        assert entry is not None
        crash = acct.apply(_alert("close", 50.0))
        assert crash is not None
        self.assertTrue(acct.halted)
        self.assertLess(acct.equity(50.0), 10_000.0 * 0.75)
        after = acct.apply(_alert("buy", 50.0, 45.0))
        assert after is not None
        self.assertEqual(after.qty, 0.0)
        self.assertIn("halted", after.note)

    def test_fee_charged(self) -> None:
        acct = MirrorAccount(initial_cash=10_000.0, fee_bps=10.0)
        fill = acct.apply(_alert("buy", 100.0, stop=None))
        assert fill is not None
        # all-in notional 9500 (95% cap), 10 bps fee = 9.5
        self.assertAlmostEqual(fill.fee, 9.5, places=6)


if __name__ == "__main__":
    unittest.main()
