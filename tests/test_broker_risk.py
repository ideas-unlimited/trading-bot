import unittest
from datetime import datetime, timedelta, timezone

from bot.broker import Broker
from bot.config import CostConfig, RiskConfig
from bot.risk import RiskManager

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestBroker(unittest.TestCase):
    def test_buy_applies_slippage_and_fee(self):
        b = Broker(cash=1000.0, costs=CostConfig(fee_bps=10, slippage_bps=50))
        fill = b.buy(T0, 100.0, 1.0)
        assert fill is not None
        self.assertAlmostEqual(fill.price, 100.5)  # 50 bps worse
        self.assertAlmostEqual(fill.fee, 0.1005)  # 10 bps of 100.5
        self.assertAlmostEqual(b.cash, 1000 - 100.5 - 0.1005)
        self.assertAlmostEqual(b.qty, 1.0)

    def test_cannot_spend_more_than_cash(self):
        b = Broker(cash=100.0, costs=CostConfig(fee_bps=10, slippage_bps=0))
        fill = b.buy(T0, 100.0, 5.0)
        assert fill is not None
        self.assertLess(fill.qty, 1.0)
        self.assertGreaterEqual(b.cash, 0.0)

    def test_zero_cash_buy_returns_none(self):
        b = Broker(cash=0.0, costs=CostConfig())
        self.assertIsNone(b.buy(T0, 10.0, 1.0))

    def test_sell_more_than_held_is_clamped(self):
        b = Broker(cash=1000.0, costs=CostConfig(fee_bps=0, slippage_bps=0))
        b.buy(T0, 10.0, 2.0)
        fill = b.sell(T0 + timedelta(hours=1), 10.0, qty=50.0)
        assert fill is not None
        self.assertAlmostEqual(fill.qty, 2.0)
        self.assertFalse(b.in_position)

    def test_round_trip_pnl_matches_cash_change(self):
        b = Broker(cash=1000.0, costs=CostConfig(fee_bps=10, slippage_bps=5))
        b.buy(T0, 100.0, 5.0)
        b.sell(T0 + timedelta(hours=1), 110.0)
        self.assertEqual(len(b.trades), 1)
        trade = b.trades[0]
        self.assertAlmostEqual(b.cash - 1000.0, trade.pnl, places=6)
        self.assertGreater(trade.pnl, 0)

    def test_average_entry_on_scale_in(self):
        b = Broker(cash=10_000.0, costs=CostConfig(fee_bps=0, slippage_bps=0))
        b.buy(T0, 100.0, 1.0)
        b.buy(T0, 200.0, 1.0)
        self.assertAlmostEqual(b.entry_price, 150.0)
        b.sell(T0, 150.0)
        self.assertAlmostEqual(b.trades[0].pnl, 0.0, places=9)

    def test_equity_is_cash_plus_mark_to_market(self):
        b = Broker(cash=1000.0, costs=CostConfig(fee_bps=0, slippage_bps=0))
        b.buy(T0, 10.0, 10.0)
        self.assertAlmostEqual(b.equity(12.0), 1000 - 100 + 120)


class TestRisk(unittest.TestCase):
    def test_sizing_risks_requested_fraction(self):
        rm = RiskManager(RiskConfig(risk_per_trade=0.02, max_position_pct=1.0))
        qty = rm.size(equity=10_000, price=100.0, stop_loss=90.0)
        # risk $200 over $10/unit -> 20 units
        self.assertAlmostEqual(qty, 20.0)

    def test_position_cap_applies(self):
        rm = RiskManager(RiskConfig(risk_per_trade=0.5, max_position_pct=0.25))
        qty = rm.size(equity=10_000, price=10.0, stop_loss=9.99)
        self.assertAlmostEqual(qty * 10.0, 2_500.0)

    def test_no_stop_falls_back_to_allocation_cap(self):
        rm = RiskManager(RiskConfig(max_position_pct=0.5))
        qty = rm.size(equity=10_000, price=20.0, stop_loss=None)
        self.assertAlmostEqual(qty * 20.0, 5_000.0)

    def test_tiny_position_is_skipped(self):
        rm = RiskManager(RiskConfig(risk_per_trade=0.00001, max_position_pct=1.0))
        self.assertEqual(rm.size(equity=100.0, price=10.0, stop_loss=9.9), 0.0)

    def test_kill_switch_trips_on_drawdown_and_stays_tripped(self):
        rm = RiskManager(RiskConfig(max_drawdown_stop=0.2))
        rm.update_equity(10_000)
        rm.update_equity(7_900)
        self.assertTrue(rm.halted)
        rm.update_equity(20_000)  # recovery does not re-enable trading
        self.assertTrue(rm.halted)
        self.assertEqual(rm.size(20_000, 10.0, 9.0), 0.0)


if __name__ == "__main__":
    unittest.main()
