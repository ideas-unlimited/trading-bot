# William_Vix_Fix_Stoch - re-analysis (2026-11-27)

> Historical study: measured on the old three-year cap. The current cap is two
> years; re-run before comparing it with a live result.

Implementation: `src/bot/strategy/wvfx_stoch.py` (registry name `wvfx_stoch`).
Tests: `tests/test_wvfx_stoch.py` (19 cases). Research simulator:
`research/wvfx_portfolio.py`, runnable headline: `research/wvfx_report.py`.

## The setup

User's three conditions, combined with the two TradingView studies:

1. **WVF green bar** - `wvf >= upper_band` or `wvf >= range_high`.
2. **Stochastic oversold** - %K below 20 at some point in the last 5 bars.
3. **Trigger** - %K at/above %D on the first green bar of the cluster.

The important re-interpretation: condition 1 is the **first green bar**, not
"any green bar in the last five". A panic cluster can stay green for many
bars; entering every one of them loses badly.

## Data and execution

- Binance 4h and 1d klines for BTC, ETH, BNB, SOL, XRP.
- Window: **2023-11-26 -> 2026-11-26** (6,577 4h bars; 1,097 1d bars).
  The raw CSV had older 2018+ data; the store now retains the last two years.
- Signal at bar close, fill next bar open. Stops/targets are anchored to the
  **signal close** (matching `Engine`), stop checked before target intrabar.
- Shared $1,000 book: 1.5% equity risk/trade (headline), max 3 positions,
  34% notional cap/position, 2xATR stop, 2R target, 40-bar time exit,
  no cross exit, no fees unless stated.

## Headline results (4h, 3y, shared $1,000)

| Variant | Trades | Return | Max DD | Sharpe | Win | Avg R | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| First green + cross + 200d filter | 72 | +45.47% | -8.29% | 1.35 | 56.9% | +0.428 | 2.02 |
| Same, 15 bps/side | 72 | +36.48% | -8.91% | 1.13 | 56.9% | +0.419 | 2.00 |
| First green + state + 200d filter | 106 | +50.09% | -13.79% | 1.09 | 51.9% | +0.290 | 1.62 |
| First green + cross, all regimes | 144 | +47.79% | -17.65% | 0.97 | 44.4% | +0.180 | 1.34 |
| 1d first green + cross + filter | 16 | +7.24% | -11.24% | 0.46 | 43.8% | +0.256 | 1.50 |

The 200-day own-coin bear filter is the risk overlay: it does not add much
return, it cuts drawdown roughly in half and lifts Sharpe. The state-only
trigger (the user's literal "blue >= red") still works, but the actual cross
has about half the drawdown for the same return. That is why the default is
`trigger="cross"`; use `--param trigger=state` for the literal version.

## Walk-forward

IS: 2023-11-26 -> 2025-11-26. OOS: 2025-11-26 -> 2026-11-26. 12-config grid
(bear on/off, rr 1.5/2.0/2.5, hold 40/50), all with signal-close stops.

- Best IS by Sharpe: bear on, rr 2.5, hold 50 -> IS +21.10%, DD -8.66%,
  Sharpe 1.09; **OOS +32.50%, DD -6.12%, Sharpe 1.84**, 37 trades,
  64.9% win, +0.643 average R.
- All 12 configs had positive OOS returns; mean +32.52%, median +32.84%,
  mean OOS Sharpe 1.90. The configs are highly correlated, so this is
  robustness, not 12 independent proofs.

## Why the old implementation failed

The old code treated any green bar in a rolling 5-bar window as the flash.
On the same 3y 4h data that version loses **-45.1%** (no cross exit) or
**-27.6%** (cross exit), with -52.3% / -37.8% drawdowns, at the same 2% risk.
Entering the first green bar only turns the same idea into +47.8% all-regime
at 2% risk, or +51.8% with the bear filter. The event study showed
the edge is concentrated on the first green bar: delaying the cross by 2-3
bars collapses the expected R to +0.03 / negative.

## The 70% win-rate gate

The default 2R target wins **56.9%**, not 70%. A tight target mechanically
raises the hit rate: `rr=0.5` wins **76.7%**, but only returns **+10.02%**
over three years at 1% risk with a -5.04% drawdown. `rr=0.75` wins 68.5% and
returns +13.67%. This is target distance, not a stronger directional edge;
the earlier conviction model had AUC 0.485-0.520, i.e. no genuine 70%
per-trade predictor. If the 70% gate is non-negotiable, use `rr=0.5`; the
default deliberately optimises expectancy instead.

## $1,000 allocation caveat

The shared book risks 1.5% of the whole wallet per trade and can hold three
positions. Splitting the same $1,000 into five $200 sleeves (2% risk each)
returns only **+12.01%** over the same 3y with the same signals: BTC +7.49%,
ETH +9.56%, BNB -3.66%, SOL +24.44%, XRP +22.23%. The shared-wallet +45% is
not free - it concentrates roughly 3.75x more dollar risk per trade and
selects the strongest concurrent signals. Size accordingly; 1% risk gives
about +26.6% after 15 bps fees and a -6.4% drawdown.

## Reproduce

```powershell
cd C:\Git\trading-bot
.venv\Scripts\python.exe -u research/wvfx_report.py
$env:PYTHONPATH='src'
.venv\Scripts\python.exe -m bot backtest --source csv `
  --csv data/crypto/BTCUSDT_4h.csv --strategy wvfx_stoch `
  --initial-cash 1000 --risk-per-trade 0.01 --max-position-pct 0.34
```

The CLI now caps every backtest/paper run to the last 2 years (`--max-years`
defaults to 2; `0` disables it for explicit research).

## Honest caveats

- 72 trades over ~2.4 years after the 200-day warmup; 2026 contributes most
  of the PnL. This is promising, not proven.
- 1d has no edge (+7.2% over 3y, -11.2% DD, 16 trades). The strategy should
  be run on 4h only.
- The filter is long-only and counter-trend: it buys capitulation below the
  200-day SMA. In a strong uptrend it sits out, which is intentional.
- No live broker path; paper/backtest only.