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

## Crypto swing / rotation playbook

Research on Binance bars (BTC/ETH/BNB/XRP/SOL) lives in `research/`. All
backtests and strategy selection are capped at the last two years - nothing
older than `MAX_HISTORY_DAYS` is loaded. A coin with less history uses
everything since its listing.

> The return figures below were measured under the previous three-year cap.
> They are retained as history; re-runs now use the two-year window.

The rule that survived a 1,750-config search on the last 12 months and the
old 3-year re-check is a 4h SMA 30/50 cross, 2 ATR stop, one position at a time,
gated on the signal bar by two regime filters ported from a Pine library:
the Damiani Volatmeter must be green (volatility awake) and the Schaff Trend
Cycle must be at or above 50. Filters only ever *block* an entry - exits are
untouched, so a filter can never strand a position.

```bash
python research/fetch_binance.py          # refresh data/crypto/*.csv (1d and 4h)

python -m bot book --timeframe 4h --fast 30 --slow 50 --atr-mult 2.0 \
                   --risk-per-trade 0.03 --damiani-filter --stc-min 50 \
                   --initial-cash 1000 --fee-bps 10
```

One $1,000 wallet across the five coins, 3% risk per trade, 10 bps fees and
5 bps slippage: **+581.6%** over three years (CAGR 89.5%, Sharpe 1.83,
drawdown -30.5%, 60 round trips, exposure 39%, every one of the five coins
profitable); **+31.0%** in the last 12 months (buy & hold -42.6%), Sharpe
1.22, drawdown -12.8%. Without the filters the same rule makes **+443.1%**
over three years (Sharpe 1.55, drawdown -30.2%, 104 round trips) and
**+41.4%** in the last year - more trades, more recent return, weaker
risk-adjusted numbers, and it loses money on BTC and BNB over the window.
The filters buy the better Sharpe and the flatter coin mix, and cost recent
return.

Faster charts do not pay. Under the sub-4h rule (one year only) the same
logic on 1h best-ed at **+6%** with a -15.8% drawdown (120/200 gated) and on
15m at **+11%** (960/1600 gated, 11 trades) against **+31%** for the 4h book
over the identical twelve months. Fees decide it: 624 round trips on 15m cost
$474 of a $1,000 account, and 1h/15m plain variants lose 34-86% of the
account. Choppy-bar noise plus 20 bps a round trip is not an edge you can
tune away.

Risk per trade runs into the position cap, and the cap, not the risk budget,
decides size on most entries: at a 50% cap every risk setting from 2% to 6%
produces the identical book ($4,074, -22.1% drawdown), because the cap always
binds first. At the 95% cap, raising risk from 3% to 6% buys roughly another
140 points of three-year return for four points of drawdown - and the
last-12-month figure peaks at 3%, then slowly decays (+31.0%, +30.0%, +30.0%,
+29.0%). 3% is the most recent-year-efficient unit; 5% is the most dollars
without a materially worse profile.

The tail still pays for everything: over three years the single best trade is
51% of all profit, and in the last 12 months one ETH trade is 89% of the
year. The earlier write-up on the raw 2% rule is `runs/report/year.html`.

Two older rules are kept as alternatives:

```bash
# core: top-2 60-day momentum, weekly, BTC > 200d SMA gate, 40% vol target
python -m bot rotate --coins BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT \
                     --initial-cash 1000 --fee-bps 10

# satellite: momentum breakout, 2.5 ATR initial stop, 3.5 ATR trailing stop,
# no hard target (trail_atr=0 or reward_risk=N restore a fixed exit)
python -m bot backtest --source csv --csv data/crypto/BTCUSDT_1d.csv \
                       --strategy momentum_swing --risk-per-trade 0.02
```

On the same three-year window the rotation core returned +119.7% with a
-29.7% drawdown, so the 4h book is the default. All of these win less than
half their trades - the edge is payoff asymmetry, not hit rate.

## Binance spot paper trading (demo account)

Signals read **production** Binance klines (`api.binance.com`, public, no key);
orders go to **demo trading** (`https://demo-api.binance.com`) with demo keys.
There is no production order path in the code, and no futures, margin or
shorts — spot, long only.

1. Create demo-trading API keys in the Binance app (Demo Trading → API
   management).  They authenticate against demo only and hold virtual
   balances.
2. Copy `.env.example` to `.env` and set `BINANCE_DEMO_KEY` /
   `BINANCE_DEMO_SECRET` (plus `DATABASE_URL`).  `.env` is git-ignored.
3. Verify:

```bash
python -m bot account                     # demo balances + open positions
```

**Backtest on real bars** (no key needed; history is capped at two years):

```bash
python -m bot backtest --source binance --symbol BTC/USDT --interval 1d --start 2024-01-01 --strategy sma_cross
python -m bot backtest --source binance --symbol ETH/USDT --interval 4h --strategy sma_cross
```

**Rank strategies across timeframes** (the strategy/timeframe scan):

```bash
python -m bot scan --symbol BTC/USDT --timeframes 1h,4h,6h,1d --strategies sma_cross --days 365
python -m bot scan --symbol BTC/USDT,ETH/USDT --timeframes 4h --strategies sma_cross,rsi_meanrev
```

Writes a ranked table (by Sharpe) and `runs/scan.csv`.  Bars are cached in
Postgres and delta-synced, so repeated scans cost no API calls.  `6h` is a
native Binance timeframe.

**Paper-trade live** — the loop polls freshly-closed production bars, computes
signals with the same no-lookahead semantics as the backtester, sizes from the
stop distance, and sends market orders to the demo account.  Spot has no
bracket orders, so the ATR stop is checked locally on each closed bar:

```bash
python -m bot live --symbol BTC/USDT --interval 1h --strategy sma_cross --poll 60
python -m bot live --symbol BTC/USDT --interval 1d --strategy sma_cross --once   # single dry cycle
```

State (last processed bar, local stops) lives in `runs/live_state.json`.

## TradingView (optional companion)

`pine/` contains Pine Script v5 ports of the strategies for TradingView's
Strategy Tester and its paper-trading broker emulation, plus a webhook
payload.  `python -m bot tv-bars` downloads TradingView bars via the
unofficial `tvDatafeed` package (`pip install tradingview-datafeed`), and
`python -m bot serve-alerts` mirrors TradingView paper-fill alerts into this
engine's risk rules on simulation money.  See `pine/README.md`.  Webhook
alerts require a paid TradingView plan; the Binance route above needs none.

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

## Running the books live (paper)

Three wallets of $1,000 each, replayed forward on Binance 4h bars:

```bash
start_bots.cmd            # background loop, one cycle every 15 min
start_bots.cmd 300        # or pick the interval (seconds)
python -m bot auto --once # single cycle, for Task Scheduler
```

| book | rule | wallet |
|------|------|--------|
| A | 4h SMA 30/50, Damiani + STC gate, 3% risk | $1,000 |
| B | same signals, 5% risk | $1,000 |
| C | plain 4h cross, no filters, 3% risk | $1,000 |

Nothing is ordered. Each cycle delta-syncs the bars cache, cuts the bar that
is still forming, replays every book over the two-year window and writes
`runs/autobooks/status.json` plus `runs/autobooks/<book>/{equity,trades}.csv`.
The replay is deterministic, so a restart cannot corrupt a wallet; the number
that matters forward is **live since deploy**, which starts when the book
first runs. The `history` line is the backtest, shown for context only.

Run the cycle just after a 4h close (00:00, 04:00, 08:00, 12:00, 16:00,
20:00 UTC). The replay always fills at the next bar's true open, so a late
poll matches the backtest exactly - which is also its one optimism: a real
order placed four hours late would not get that price. Prompt polling keeps
the paper book honest rather than flattering.

