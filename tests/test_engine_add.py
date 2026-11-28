"""DCA adds and price-armed trails in the engine."""

from __future__ import annotations

import unittest

import pandas as pd

from bot.backtest import Engine
from bot.config import BotConfig, CostConfig
from bot.strategy.base import Action, Signal, Strategy


def bars(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=len(prices), freq="D"),
        "open": prices, "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices], "close": prices, "volume": 100.0,
    })


def run(df: pd.DataFrame, strategy: Strategy) -> Engine:
    cfg = BotConfig(initial_cash=10_000.0, costs=CostConfig(fee_bps=0, slippage_bps=0))
    eng = Engine(df, strategy, cfg)
    eng.run()
    return eng


class Ladder(Strategy):
    """Three 1/3-sized buys at the open of bars 2, 3 and 4."""

    name = "ladder"
    PARAMS = ("add",)

    def __init__(self, add: bool = True):
        super().__init__(add=add)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = 2.0
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        if i in (1, 2, 3):
            return Signal(
                Action.BUY,
                reason=f"tranche {i}",
                stop_loss=90.0,
                add=self.add and i > 1,
                size_fraction=1 / 3,
            )
        return None


class ArmPrice(Strategy):
    """Buy once, then arm a 2-ATR trail when price touches arm."""

    name = "arm_price"
    PARAMS = ("arm",)

    def __init__(self, arm: float | None = 104.0):
        super().__init__(arm=arm)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = 2.0
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        if position == 0 and i == 1:
            return Signal(
                Action.BUY, "test", stop_loss=90.0,
                trail_atr=2.0 if self.arm is not None else None,
                trail_after_price=self.arm,
            )
        return None


class LadderLimit(Strategy):
    """A resting three-level limit ladder."""

    name = "ladder_limit"
    PARAMS = ()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"] = 2.0
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        if i == 1 and position == 0:
            return Signal(
                Action.BUY, "ladder", stop_loss=80.0, size_fraction=1 / 3,
                limit_prices=(100.0, 95.0, 90.0),
                limit_expires_at=pd.Timestamp("2030-01-01"),
            )
        return None


class AddTests(unittest.TestCase):
    def test_three_tranches_share_one_weighted_position(self) -> None:
        eng = run(bars([100, 100, 100, 100, 100, 80]), Ladder())
        buys = [f for f in eng.broker.fills if f.side == "buy"]
        self.assertEqual(len(buys), 3)
        self.assertEqual(len(eng.broker.trades), 1)
        self.assertAlmostEqual(eng.broker.trades[0].entry_price, 100.0, places=6)

    def test_add_false_is_refused_while_in_position(self) -> None:
        eng = run(bars([100, 100, 100, 100, 100, 80]), Ladder(add=False))
        buys = [f for f in eng.broker.fills if f.side == "buy"]
        self.assertEqual(len(buys), 1)

    def test_limit_ladder_fills_each_touched_level(self) -> None:
        eng = run(bars([100, 100, 100, 95, 90, 100]), LadderLimit())
        buys = [f for f in eng.broker.fills if f.side == "buy"]
        self.assertEqual(len(buys), 3)
        # Sizing is risk-based, so the lower tranches are larger; the weighted
        # entry must sit between the lowest and highest fills.
        self.assertLess(eng.broker.trades[0].entry_price, 95.0)
        self.assertGreater(eng.broker.trades[0].entry_price, 90.0)

    def test_price_armed_trail_waits_for_the_level(self) -> None:
        prices = [100, 100, 100, 105, 95, 115, 111]
        armed = run(bars(prices), ArmPrice(arm=104.0))
        fixed = run(bars(prices), ArmPrice(arm=None))
        self.assertEqual(len(armed.broker.trades), 1)
        self.assertEqual(len(fixed.broker.trades), 1)
        self.assertLess(armed.broker.trades[0].exit_price, 100.0)
        self.assertGreater(fixed.broker.trades[0].exit_price, 110.0)


if __name__ == "__main__":
    unittest.main()
