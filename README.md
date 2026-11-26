# trading-bot

A small, honest framework for developing and evaluating a trading strategy:
**backtest → measure → paper trade**.  Everything runs offline on simulation
money; there is deliberately no code path that can place a real order.

> **Not investment advice.** Most retail trading bots lose money after fees,
> spread and slippage. Use this to learn and to falsify ideas quickly — with
> money you can afford to lose, if you ever go live.

## Why it is built this way

A backtest is only useful if it refuses to lie to you. The engine enforces:

| Rule | Where |
| --- | --- |
| Signals are computed on bar *i*'s close but **filled at bar *i+1*'s open** — no lookahead | `backtest.Engine` |
| Fees on **both** sides and adverse slippage on **both** sides | `broker.Broker` |
| Stops fill at the stop, or worse if the bar gaps through it | `backtest.Engine._check_exits` |
| Position size derived from the distance to the stop (volatility-aware) | `risk.RiskManager.size` |
| Cash can never go negative; no leverage by default | `broker.Broker.buy` |
| Trading halts after a max-drawdown breach | `risk.RiskManager.update_equity` |
| Strategies cannot trade before indicators are warm | `Strategy.warmup` |

A useful sanity check: on this project's synthetic **random walk**, the trend
strategy's return hovers around zero minus costs. A backtester that reports
large profits on random data has a bug (typically lookahead).

## Quickstart

Requires Python 3.11+ with `pandas` and `numpy` (`matplotlib` only for charts).

```bash
export PYTHONPATH=src          # Windows bash: same; PowerShell: $env:PYTHONPATH="src"
# or: pip install -e .

python -m bot strategies
python -m bot gen-data --bars 5000 --out data/sample.csv
python -m bot backtest --source csv --csv data/sample.csv --strategy sma_cross --plot
python -m bot paper    --source synthetic --strategy rsi_meanrev --delay 0.05
python -m unittest discover -s tests
```

`backtest` writes `runs/trades.csv` and (with `--plot`) `runs/equity.png`.
`paper` replays bars through the same engine and narrates fills — no orders.

## What the output looks like

```
Strategy        sma_cross
Period          2023-01-01 00:00 -> 2023-07-28 07:00  (5,000 bars)
Start equity    10,000.00
End equity      7,541.55

  Total return               -24.58%
  Buy & hold                 -52.32%
  CAGR                       -39.02%
  Sharpe (annualised)          -4.50
  Max drawdown               -25.11%
  Round trips                     41
  Win rate                    14.63%
  Profit factor                 0.17
  Avg exposure                11.50%
!! trading halted: max drawdown 25.1% reached
```

Note what the halt does: entries stop, the curve goes flat, and the loss stays
bounded. That is the risk layer doing its job, not a bug.

## Layout

```
src/bot/
  config.py        cost + risk settings (frozen dataclasses)
  backtest.py      bar-by-bar engine, next-bar fills, intrabar stops
  broker.py        long-only fills, fees, slippage, round-trip accounting
  risk.py          stop-based sizing, position caps, drawdown kill switch
  metrics.py       return/CAGR/Sharpe/Sortino/drawdown/win-rate/profit factor
  report.py        summary text, trades csv, equity chart
  paper.py         live-ish replay through the same engine
  cli.py           gen-data | strategies | backtest | paper
  data/feed.py     Bar type, csv loader, deterministic synthetic generator
  strategy/        base interface + registry + two example strategies
tests/             58 unittest cases, no network
```

## Adding a strategy

1. Subclass `Strategy` and list your tunables in `PARAMS`.
2. Add indicator columns in `prepare` (vectorised, past-only).
3. Return a `Signal` from `signal(i, df, position)` — `BUY` with a `stop_loss`,
   `CLOSE`, or `None`.
4. Register it in `strategy/__init__.py`.

```python
from bot.strategy.base import Action, Signal, Strategy
from bot import indicators as ind

class Donchian(Strategy):
    name, PARAMS = "donchian", ("window",)
    def __init__(self, window: int = 55):
        super().__init__(window=window); self.window = window
        self.warmup = window + 1

    def prepare(self, df):
        out = df.copy()
        out["hi"] = out["high"].rolling(self.window).max().shift(1)
        out["lo"] = out["low"].rolling(self.window).min().shift(1)
        return out

    def signal(self, i, df, position):
        row = df.iloc[i]
        if position == 0 and row["close"] > row["hi"]:
            return Signal(Action.BUY, "breakout", stop_loss=float(row["lo"]))
        if position > 0 and row["close"] < row["lo"]:
            return Signal(Action.CLOSE, "breakdown")
```

```bash
python -m bot backtest --strategy my_strategy --param window=20
```

## Using real data

The csv loader accepts `timestamp,open,high,low,close[,volume]` with ISO dates
or unix seconds/milliseconds:

```bash
python -m bot backtest --source csv --csv my_bars.csv --fee-bps 7 --slippage-bps 3
```

Gaps matter more than volume: forward-fill missing bars or drop them, but never
interpolate across a session break — the stop logic assumes bars are contiguous.
Annualisation is inferred from the median bar spacing, so hourly and daily files
both work.

## Plugging in a live feed

`Engine` is stateful and advances one bar per `step()`, so a live loop is:

```python
engine = Engine(bars_to_frame(bars_so_far), strategy, config)
while True:
    engine.df = strategy.prepare(bars_to_frame(recent_bars))  # rolling window
    engine.step()
    broker_state = engine.broker
```

`PaperTrader` is the working example. Going from there to real orders means
writing an exchange adapter around `Broker.buy`/`Broker.sell` and reconciling
fills — at that point, start with the smallest size the exchange allows and
verify that simulated and live fills agree before scaling.

## Known limits (on purpose)

- **Long-only**, one position at a time: shorting and margin need funding-cost
  and liquidation models to be honest.
- **Bar data only**: no order book, no partial fills, no queue position, no
  latency. Short-horizon strategies will look far better here than in reality.
- **Fixed slippage**: real slippage scales with size and volatility.
- **No walk-forward or parameter search**: both are easy to do badly (overfitting)
  and deserve their own harness.
- **Synthetic data is a random walk**, not a market. Do not tune against it.

Next steps worth doing, in order: walk-forward validation on real data, an
out-of-sample holdout, then capital-scaled slippage.
