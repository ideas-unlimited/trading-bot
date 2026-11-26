import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from bot.backtest import Engine, bars_to_frame, run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.data.feed import Bar, generate_synthetic
from bot.metrics import compute_metrics
from bot.strategy.base import Action, Signal, Strategy

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_bars(rows, step=timedelta(hours=1)):
    """rows: list of (open, high, low, close)."""
    return [
        Bar(ts=T0 + step * i, open=o, high=h, low=l, close=c, volume=1.0)
        for i, (o, h, l, c) in enumerate(rows)
    ]


class Scripted(Strategy):
    name = "scripted"
    PARAMS = ()

    def __init__(self, signals: dict[int, Signal]):
        super().__init__()
        self.signals = signals
        self.seen: list[tuple[int, float]] = []

    def prepare(self, df):
        return df

    def signal(self, i, df, position):
        self.seen.append((i, float(df.iloc[i]["close"])))
        return self.signals.get(i)


FREE = BotConfig(
    initial_cash=1000.0,
    costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),
    risk=RiskConfig(risk_per_trade=1.0, max_position_pct=1.0, max_drawdown_stop=1.0),
)


class TestEngineExecution(unittest.TestCase):
    def test_signal_fills_at_next_open(self):
        rows = [(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102),
                (105, 106, 104, 105), (105, 105, 105, 105)]
        strat = Scripted({2: Signal(Action.BUY, "go")})
        engine = Engine(bars_to_frame(make_bars(rows)), strat, FREE)
        engine.run()

        self.assertEqual(len(engine.broker.fills), 2)  # entry + forced exit
        entry = engine.broker.fills[0]
        self.assertEqual(entry.ts, T0 + timedelta(hours=3))
        self.assertAlmostEqual(entry.price, 105.0)  # open of bar 3, not close of bar 2

    def test_strategy_never_sees_future_bars(self):
        bars = make_bars([(100, 100, 100, 100)] * 6)
        strat = Scripted({})
        engine = Engine(bars_to_frame(bars), strat, FREE)
        engine.run()
        # signal() is only called for bars available so far, in order.
        self.assertEqual([i for i, _ in strat.seen], list(range(len(bars))))

    def test_stop_loss_fills_intrabar(self):
        rows = [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal here
            (100, 101, 99, 100),   # entry at 100
            (100, 100, 90, 92),    # stop 95 must fire
            (92, 92, 92, 92),
        ]
        strat = Scripted({1: Signal(Action.BUY, "go", stop_loss=95.0)})
        engine = Engine(bars_to_frame(make_bars(rows)), strat, FREE)
        engine.run()

        buy, sell = engine.broker.fills
        self.assertEqual(buy.side, "buy")
        self.assertAlmostEqual(buy.price, 100.0)
        self.assertEqual(sell.side, "sell")
        self.assertAlmostEqual(sell.price, 95.0)
        self.assertIn("stop", sell.reason)
        self.assertEqual(len(engine.broker.trades), 1)
        self.assertAlmostEqual(engine.broker.trades[0].pnl, -5.0 * buy.qty)

    def test_stop_gaps_through_uses_worse_open(self):
        rows = [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal
            (100, 100, 100, 100),  # entry
            (90, 91, 88, 89),      # gaps below the 95 stop
        ]
        strat = Scripted({1: Signal(Action.BUY, "go", stop_loss=95.0)})
        engine = Engine(bars_to_frame(make_bars(rows)), strat, FREE)
        engine.run()
        sell = engine.broker.fills[-1]
        self.assertAlmostEqual(sell.price, 90.0)  # open, not the 95 stop

    def test_take_profit_fills_intrabar(self):
        rows = [
            (100, 100, 100, 100),
            (100, 100, 100, 100),
            (100, 101, 100, 100),
            (100, 120, 100, 118),
        ]
        strat = Scripted({1: Signal(Action.BUY, "go", take_profit=110.0)})
        engine = Engine(bars_to_frame(make_bars(rows)), strat, FREE)
        engine.run()
        self.assertAlmostEqual(engine.broker.fills[-1].price, 110.0)

    def test_open_position_is_liquidated_at_end(self):
        bars = make_bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 130)])
        engine = Engine(bars_to_frame(bars), Scripted({0: Signal(Action.BUY, "go")}), FREE)
        engine.run()
        self.assertFalse(engine.broker.in_position)
        self.assertAlmostEqual(engine.broker.fills[-1].price, 130.0)

    def test_sell_without_position_is_ignored(self):
        bars = make_bars([(100, 100, 100, 100)] * 4)
        engine = Engine(bars_to_frame(bars), Scripted({1: Signal(Action.CLOSE, "nope")}), FREE)
        engine.run()
        self.assertEqual(engine.broker.fills, [])

    def test_second_buy_while_in_position_is_ignored(self):
        bars = make_bars([(100, 100, 100, 100)] * 5)
        strat = Scripted({0: Signal(Action.BUY, "a"), 2: Signal(Action.BUY, "b")})
        engine = Engine(bars_to_frame(bars), strat, FREE)
        engine.run()
        self.assertEqual([f.side for f in engine.broker.fills].count("buy"), 1)


class TestEngineAccounting(unittest.TestCase):
    def test_cash_change_equals_realised_pnl(self):
        bars = generate_synthetic(400, seed=21)
        strat = Scripted({i: Signal(Action.BUY, "t", stop_loss=float(bars[i].close) * 0.97)
                          for i in range(0, 400, 60)})
        for i in range(0, 400, 60):
            strat.signals[i + 10] = Signal(Action.CLOSE, "exit")
        engine = Engine(bars_to_frame(bars), strat, BotConfig(initial_cash=5000.0))
        engine.run()
        result = engine.result()
        realised = sum(t.pnl for t in result.trades)
        self.assertAlmostEqual(engine.broker.cash - 5000.0, realised, places=6)
        self.assertEqual(len(result.equity_curve), len(bars))
        self.assertFalse(engine.broker.in_position)
        self.assertLessEqual(abs(result.exposure).max(), 1.0 + 1e-9)

    def test_equity_curve_never_negative(self):
        bars = generate_synthetic(600, seed=2)
        result = run_backtest(bars, Scripted({}), BotConfig(initial_cash=1000.0))
        self.assertGreater(result.equity_curve.min(), 0)

    def test_drawdown_halt_blocks_new_entries(self):
        bars = generate_synthetic(800, seed=13)
        buys = {i: Signal(Action.BUY, "dip", stop_loss=bars[i].close * 0.5)
                for i in range(0, 800, 25)}
        strat = Scripted(buys)
        cfg = BotConfig(
            initial_cash=1000.0,
            risk=RiskConfig(risk_per_trade=1.0, max_position_pct=1.0, max_drawdown_stop=0.05),
        )
        engine = Engine(bars_to_frame(bars), strat, cfg)
        engine.run()
        result = engine.result()
        self.assertTrue(engine.risk.halted)
        self.assertIn("drawdown", result.halted_reason)
        # No new entry may be filled once the drawdown limit has been breached.
        equity = result.equity_curve
        breached = (equity / equity.cummax() - 1.0) <= -0.05
        halt_ts = breached[breached].index[0]
        buy_ts = [f.ts for f in result.fills if f.side == "buy"]
        self.assertTrue(buy_ts)
        self.assertTrue(all(ts <= halt_ts for ts in buy_ts))


class TestMetrics(unittest.TestCase):
    def test_flat_equity_has_zero_return(self):
        bars = make_bars([(100, 100, 100, 100)] * 10)
        result = run_backtest(bars, Scripted({}), FREE)
        self.assertAlmostEqual(result.metrics["total_return"], 0.0)
        self.assertAlmostEqual(result.metrics["max_drawdown"], 0.0)
        self.assertEqual(result.metrics["trades"], 0)

    def test_buy_and_hold_benchmark(self):
        bars = make_bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 150)])
        result = run_backtest(bars, Scripted({}), FREE)
        self.assertAlmostEqual(result.metrics["buy_and_hold"], 0.5)

    def test_trade_stats_from_synthetic_run(self):
        bars = generate_synthetic(500, seed=8)
        strat = Scripted({0: Signal(Action.BUY, "go", stop_loss=bars[0].close * 0.98),
                          200: Signal(Action.CLOSE, "done")})
        result = run_backtest(bars, strat, BotConfig())
        self.assertEqual(result.metrics["trades"], 1.0)
        self.assertIn(result.metrics["win_rate"], (0.0, 1.0))
        self.assertGreaterEqual(result.metrics["fees"], 0.0)

    def test_metrics_render(self):
        from bot.metrics import format_metrics

        bars = make_bars([(100, 100, 100, 100)] * 10)
        text = format_metrics(run_backtest(bars, Scripted({}), FREE).metrics)
        self.assertIn("Total return", text)
        self.assertIn("Sharpe", text)


if __name__ == "__main__":
    unittest.main()


class TestAnnualisation(unittest.TestCase):
    def test_hourly_bars_infer_8766(self):
        from bot.metrics import infer_bars_per_year

        bars = make_bars([(100, 100, 100, 100)] * 10)  # 1h spacing
        self.assertEqual(infer_bars_per_year([b.ts for b in bars]), 8766)

    def test_daily_bars_infer_365(self):
        from bot.metrics import infer_bars_per_year
        from datetime import timedelta

        bars = make_bars([(100, 100, 100, 100)] * 10, step=timedelta(days=1))
        self.assertEqual(infer_bars_per_year([b.ts for b in bars]), 365)

    def test_too_few_bars_falls_back(self):
        from bot.metrics import infer_bars_per_year

        self.assertEqual(infer_bars_per_year([T0]), 365.0)
