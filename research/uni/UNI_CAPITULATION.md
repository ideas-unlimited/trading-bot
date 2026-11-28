# UNI/USDT 4h capitulation-dip sleeve

**Registry: `capitulation_dip_uni`.** **Status: paper candidate, not promoted.**
One symbol, one strategy, parameters fitted on UNI only. No pooled settings.

## The rule

On the closed 4h bar, buy when the close is at least **20% below the prior
20-bar high**. The prior high is shifted one bar, so the signal bar cannot
create its own reference.

- Stop: **2.5 x ATR(14)** below the signal close.
- Target: **1.5R** (1.5 times the stop distance).
- Time exit: **18 bars** after entry if neither stop nor target is hit.
- Long only, spot, one position at a time, next-bar-open fills.

Production module: `src/bot/strategy/capitulation_dip_uni.py` (name
`capitulation_dip_uni`). It is the only implementation: the research scripts
now import it rather than keeping a second copy. The production class
reproduces the 180-config grid byte-for-byte, not just the headline run.

## Data and costs

- Venue: Binance spot production klines, UNI/USDT.
- Window: **2024-11-28 04:00 to 2026-11-28 00:00 UTC**, 4,380 4h bars.
- Fills: signal on bar close, fill at next bar open.
- Costs: **10 bps fee + 5 bps adverse slippage on both sides**.
- Risk: 5% of equity per trade, max position **50% of equity**, 50% hard
  drawdown stop in the base run.
- No shorting, no margin, no leverage.

## Headline result

| Slice | Return | Max DD | Sharpe | Trades | Win rate | Avg trade | Profit factor | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full 2 years | **+67.8%** | **-5.3%** | 2.13 | 19 | 89.5% | +7.53% | 9.82 | 2.9% |
| Year 1 (fit) | +40.7% | -5.3% | 2.30 | 12 | 83.3% | +8.62% | 7.37 | 3.0% |
| Year 2 (holdout) | +19.3% | -3.5% | 2.03 | 7 | 100% | +5.67% | n/a | 2.7% |

CAGR 29.6%. Best trade +25.1%, worst -11.8% (a gap through the
stop). Of the 19 exits: 5 take-profit, 1 stop, 13 time exits.

Six-month folds, same rules and costs:

| Fold | Return | Max DD | Trades |
|---|---:|---:|---:|
| 2024-11-28 to 2025-05-29 | +24.6% | -5.3% | 7 |
| 2025-05-29 to 2025-11-27 | +10.0% | -3.4% | 4 |
| 2025-11-27 to 2026-05-28 | +5.6% | -3.5% | 3 |
| 2026-05-28 to 2026-11-28 | +12.9% | -2.3% | 4 |

Every slice is positive, but the trade count per slice is tiny. The holdout is
7 trades, not a statistically independent second experiment.

## Risk scaling (same trades, 50% position cap)

| Risk per trade | Return | Max DD | Sharpe |
|---:|---:|---:|---:|
| 2% | +24.4% | -2.2% | 2.15 |
| 3% | +38.5% | -3.2% | 2.16 |
| 5% | +67.8% | -5.3% | 2.13 |
| 7.5% | +86.7% | -7.4% | 2.05 |
| 10% | +94.0% | -7.4% | 2.06 |

At 10% the 50% position cap binds, so extra risk stops adding return. The
5% row is the proposed paper setting: one stop costs about 5% of the account,
and the realised max drawdown was essentially one stop.

## Cost stress (5% risk)

| Fee / slippage per side | Return | Max DD | Sharpe |
|---|---:|---:|---:|
| 10 / 5 bps | +67.8% | -5.3% | 2.13 |
| 20 / 10 bps | +64.0% | -5.3% | 2.04 |
| 30 / 15 bps | +60.3% | -5.4% | 1.95 |
| 50 / 25 bps | +53.2% | -5.6% | 1.76 |
| 100 / 50 bps | +36.5% | -6.2% | 1.29 |

The edge does not depend on near-zero costs. Even at 100 bps fee and 50 bps
slippage per side it compounds, which is the right kind of robustness for a
strategy that trades only 19 times in two years.

## Why the 20% threshold

Event study on the same 4h bars, measuring the close 18 bars later (overlapping
events, not independent trades):

| Event: close vs prior 20-bar **high** | n | Mean 18-bar return | Win rate | Year 1 | Year 2 |
|---|---:|---:|---:|---:|---:|
| <= -15% | 390 | +2.26% | 62.8% | +2.61% | +1.66% |
| <= -18% | 199 | +3.69% | 67.8% | +4.84% | +1.80% |
| <= -20% | 111 | **+5.28%** | **75.7%** | +5.34% | +5.16% |
| <= -22% | 79 | +4.99% | 74.7% | +4.67% | +5.74% |
| <= -25% | 42 | +7.04% | 85.7% | +7.79% | +5.81% |
| Unconditional | 4,362 | -0.01% | 45.7% | - | - |

There is a clear step at -20%, and the -20% row is stable across both years.
Milder dips are not the same trade: -15% averages +2.26% and decays in year 2.
This is why the sleeve waits for the violent event instead of buying every
pullback.

## Parameter robustness

A 180-config grid around the chosen point (window fixed at 20; drop 0.18-0.22;
stop 2.0-3.0 ATR; hold 12-21 bars; rr 1.5/2.0/none) was run on year 1 and
scored on year 2:

- **180 of 180 configs were positive in both halves.** Median year-2 return
  +9.2%, median year-1 +21.2%, at 5% risk and the same 50% position cap as
  the headline.
- Rank correlation between year-1 and year-2 Calmar: **0.600** (p < 1e-15).
- Best year-1 Calmar (drop 0.20, stop 2.5, hold 21, rr 1.5): year 2 +16.5%,
  -4.3% DD, 7 trades. The chosen hold 18 has the higher year-2 return
  (+19.3%) and the lower drawdown (-3.5%).
- The chosen config is not the single best cell; it is the centre of the
  plateau. That is deliberate.

Window is the one dimension not in that grid. At the chosen drop/stop/hold/rr,
window 15 gives +49.3% full / +13.4% year 2, window 25 gives +41.2% / +16.0%,
against +67.8% / +19.3% at 20. Across the nine window (15/20/25) x stop
(2.0/2.5/3.0) neighbours every result is positive (+27.6% to +67.8% full),
but window 20 is a local peak rather than a broad plateau. Treat it as a
chosen parameter, not a law.

The plateau is the main defence against having simply picked the best cell.
It is not proof of future returns: all 180 configs trade the same handful of
crash events.

## What does not work

- **1h timeframe.** At 1h, 20-bar drop thresholds of 10-15% were flat to
  negative (drop 15%, hold 18: -1.0% over the last 12 months; first half
  -5.3%, second half +4.5%). The 4h event is the tradeable one.
- **1d timeframe.** A 20-day -20% drawdown had no 6-day edge (+0.42% mean;
  first half +1.06%, second half -0.43%).
- **Volatility-normalised z-score.** Replacing the absolute -20% with a
  rolling z-score (z < -2.5) gave only +0.94% at 18 bars and was unstable
  across halves (+4.20% / -1.22%). The absolute drawdown is the better
  trigger on UNI.
- **Filters.** RSI < 30/35/40, volume z-score, and close-above-open all
  reduced trade count without improving the risk-adjusted result. The simple
  rule was kept.
- **Monotonic bleed.** On a synthetic -1.2% per 4h bar decline (no bounce),
  the sleeve loses **-26.7%** over 9 trades with 0% wins before the 25%
  kill switch stops it. This is the failure mode: the rule needs a bounce.
  A real gap through the stop can lose more than the nominal 5% risk.

## Execution notes

- Poll just after the 4h close (00/04/08/12/16/20 UTC). The backtest fills at
  the next open; a 15-minute loop is acceptable, but a late fill is not the
  same trade.
- Measured delay cost on the last 12 months' 34 -20% events: +1h average
  -0.18%, +2h +0.41%, +4h +0.53%. A one-hour delay is not a disaster, but
  prompt is better and the live fill should be measured forward.
- Median entry notional was 41% of equity; the 50% cap is what keeps one gap
  from becoming an account-level event.
- The sleeve is in cash about 97% of the time. It is a single-symbol
  satellite, not a stand-alone portfolio. Do not run five correlated alts at
  5% risk each and call the sum diversified.

## Honest caveats

1. **19 trades.** The win rate and profit factor are small-sample numbers.
   The bootstrap of the 19 realised account returns has P(sum <= 0) = 0.02%,
   but that treats the trades as independent draws from the same regime.
2. **One regime.** The whole window is a UNI crash and rebound (UNI fell
   -61.5% over the two years, with violent rallies). A slow bleed is the
   stress case, and it loses.
3. **Selection.** The rule and parameters came from a grid on this data. The
   180/180 plateau and the year-1/year-2 split are the mitigations; they are
   not a guarantee.
4. **No live routing.** Paper only. The Binance demo path exists; no real
   order path is wired and none should be added without a deliberate review.
5. **No older data.** The two-year cap means this exact rule was not tested
   through 2022 or earlier UNI regimes.

## Proposed paper configuration

- Symbol: UNI/USDT
- Timeframe: 4h
- Strategy: capitulation_dip_uni
- Params: window 20, drop_pct 0.20, stop_atr 2.5, rr 1.5, max_hold 18
- Risk: 5% per trade
- Max position: 50% of equity
- Max drawdown stop: 25% (tighter than the 50% base run)
- Review: after 20 forward trades or 3 months, whichever comes first; promote
  only if the forward sample is consistent with the backtest's trade shape
  (average winner, stop frequency, exposure), not just its return.

## Deployment status

The module is registered and backtests through the normal CLI, but it is not
running forward yet:

- **`bot auto` is hardcoded to SmaCross.** The 15-minute paper books replay a
  multi-coin SMA/trail book; there is no generic strategy hook for this
  single-symbol sleeve. A paper runner that reuses the backtest engine's exit
  logic (stop, target, time) is the missing piece.
- **`bot live` ignores take-profit on spot.** `submit_order` accepts
  `take_profit` for interface parity, but the caller only keeps the stop in
  its state file. Pointing `bot live` at rr=1.5 would silently drop the
  target and change the trade. The live-compatible variant is `rr=None`:
  +55.2%, -7.1% max DD, 19 trades, Sharpe 1.75 at the same 5% risk /
  50% position cap.
- **A restart while holding resets the hold clock.** The stop survives in the
  state file, the time exit does not. The paper book replay does not have
  this problem because it replays history deterministically, but it cannot
  run this strategy yet.
- **A drawdown stop is a brake, not a floor.** It stops new entries after
  the account is down; it does not cap the loss at 25%. The slow-bleed test
  still lost ~27% before the switch.

So: do not treat a `bot live` run with the 1.5R target as a validation of
this backtest. The next step is a paper sleeve that records simulated fills
with the same exit semantics, then 20 forward trades.

To watch the historical replay through the CLI:

    $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m bot paper --source binance `
      --symbol UNI/USDT --interval 4h --n-bars 4380 --strategy capitulation_dip_uni `
      --param window=20 --param drop_pct=0.20 --param stop_atr=2.5 `
      --param rr=1.5 --param max_hold=18 --risk-per-trade 0.05 `
      --max-position-pct 0.5 --max-drawdown-stop 0.25

## Reproduce

    $env:PYTHONPATH='src'; .venv\Scripts\python.exe research/uni/uni_final.py
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe research/uni/uni_walkforward.py
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_capitulation_dip_uni -v

The first script prints the full, train, test, fold, risk, cost, trade and
bootstrap tables. The second prints the 180-config walk-forward summary.
