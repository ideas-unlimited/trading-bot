"""Trailing-stop mechanics in the engine, with no lookahead."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.backtest import Engine
from bot.config import BotConfig, CostConfig
from bot.strategy.base import Action, Signal, Strategy


class OneShot(Strategy):
    """Buys on bar buy_at, then never signals again (the trail exits)."""

    name = "one_shot"
    PARAMS = ("buy_at", "stop", "trail")

    def __init__(self, buy_at: int = 1, stop: float = 90.0, trail: float | None = 2.0):
        super().__init__(buy_at=buy_at, stop=stop, trail=trail)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = 2.0
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        if position == 0 and i == self.buy_at:
            return Signal(Action.BUY, 'test', stop_loss=self.stop, trail_atr=self.trail)
        return None


def bars(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=len(prices), freq="D"),
        "open": prices, "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices], "close": prices, "volume": 100.0,
    })


def run(df: pd.DataFrame, strategy: OneShot) -> Engine:
    cfg = BotConfig(initial_cash=10_000.0, costs=CostConfig(fee_bps=0, slippage_bps=0))
    eng = Engine(df, strategy, cfg)
    eng.run()
    return eng


class TrailingTests(unittest.TestCase):
    def test_stop_ratchets_up_and_exits_above_entry(self) -> None:
        # Entry 100 (bar 2 open).  Peak 120.5 -> stop 116.5 with 2 ATR of 2.
        prices = [100, 100, 100, 110, 120, 118, 116]
        eng = run(bars(prices), OneShot(buy_at=1))
        trades = eng.broker.trades
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0].entry_price, 100.0, places=6)
        self.assertGreater(trades[0].exit_price, 110.0)
        self.assertLess(trades[0].exit_price, 120.0)

    def test_trail_never_moves_down(self) -> None:
        # The 120 peak sets 116.5; the drop to 110 must exit there, not wait
        # for the later 130 high.
        prices = [100, 100, 100, 120, 110, 115, 111, 130, 128]
        eng = run(bars(prices), OneShot(buy_at=1))
        self.assertEqual(len(eng.broker.trades), 1)
        self.assertLessEqual(eng.broker.trades[0].exit_price, 110.5)

    def test_no_trail_means_fixed_stop(self) -> None:
        prices = [100, 100, 100, 120, 118, 112, 105, 99, 85]
        eng = run(bars(prices), OneShot(buy_at=1, trail=None))
        trades = eng.broker.trades
        self.assertEqual(len(trades), 1)
        self.assertLessEqual(trades[0].exit_price, 90.5)

    def test_gap_through_fixed_stop_fills_at_open(self) -> None:
        prices = [100, 100, 100, 95, 85]
        eng = run(bars(prices), OneShot(buy_at=1, trail=None))
        trade = eng.broker.trades[0]
        self.assertLessEqual(trade.exit_price, 85.5)


if __name__ == "__main__":
    unittest.main()
