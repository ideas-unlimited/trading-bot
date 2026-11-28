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
- Window: **2024-11-28 04:00 to 2026-11-28 00:00 UTC**, 4,381 4h bars.
- Fills: signal on bar close, fill at next bar open.
- Costs: **10 bps fee + 5 bps adverse slippage on both sides**.
- Risk: 5% of equity per trade, max position **50% of equity**, 50% hard
  drawdown stop in the base run.
- No shorting, no margin, no leverage.

## Headline result

Verified 2026-11-28 by replaying the shipped module over the cached bars:
**+67.8%**, **-5.27%** max drawdown, 19 round trips, 89.5% wins, 4,381 bars
(`python -m bot backtest --source binance --symbol UNI/USDT --interval 4h
--start 2024-11-28 --strategy capitulation_dip_uni --risk-per-trade 0.05
--max-position-pct 0.5 --fee-bps 10 --slippage-bps 5`).

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

## What 1.5R means in price

R is the distance from entry to stop, so every exit scales with volatility
instead of a fixed percentage:

- stop = entry - 2.5 x ATR14, so **1R = 2.5 ATR** below entry;
- target = entry + 1.5R = entry + **3.75 ATR**;
- time exit = 18 bars after entry = **72 hours** on 4h bars.

On UNI 4h one ATR14 was ~5.4% of price over this sample, so the median trade
risked **12.4%** of price to make **18.6%** (the target is 1.5x the risk;
mean stop 13.5%, mean target 20.3%). The stop is deliberately wide - a normal
4h bar is ~5% - which is why size is small: 5% of equity risk, and notional
runs 30-50% of equity, with the 50% cap binding once the wallet grows.

First trade on exactly $1,000: entry 14.689, stop 12.178 (-$2.51/UNI), target
18.455 (+$3.77). Risk $50 buys 19.9 UNI ($292 notional); the target paid **+$74**
(+1.49R after costs). By 2026-11-20 the wallet was ~$1.58k, so the last trade
risked ~$79: entry 4.354, stop 3.910 (-10.2%), target 5.020 (+15.3%), high
tagged it 64h later, exit 5.013 = **+$115 (+1.48R)**.

Every trade in price terms (P&L scaled to a $1,000 start; the reported run
started at $10,000 and the percentages are identical):

| entry | entry px | stop | target | exit | kind | R | P&L |
|---|---:|---:|---:|---|---|---:|---:|
| 2024-12-10 | 14.689 | 12.178 (-17.1%) | 18.455 (+25.6%) | 2024-12-12 | take | +1.49R | +74 |
| 2024-12-19 | 13.164 | 11.193 (-15.0%) | 16.120 (+22.5%) | 2024-12-23 | time | +0.37R | +19 |
| 2025-01-27 | 10.500 | 9.531 (-9.2%) | 11.954 (+13.8%) | 2025-01-29 | take | +1.45R | +72 |
| 2025-02-02 | 9.585 | 8.385 (-12.5%) | 11.385 (+18.8%) | 2025-02-03 | stop | -0.93R | -60 |
| 2025-02-03 | 8.134 | 6.433 (-20.9%) | 10.686 (+31.4%) | 2025-02-06 | time | +0.71R | +43 |
| 2025-03-11 | 5.712 | 5.005 (-12.4%) | 6.773 (+18.6%) | 2025-03-14 | time | +0.23R | +12 |
| 2025-04-07 | 4.705 | 4.172 (-11.3%) | 5.505 (+17.0%) | 2025-04-09 | take | +1.45R | +86 |
| 2025-05-31 | 5.998 | 5.289 (-11.8%) | 7.062 (+17.7%) | 2025-06-03 | time | +0.56R | +33 |
| 2025-06-22 | 6.193 | 5.489 (-11.4%) | 7.249 (+17.1%) | 2025-06-25 | take | +1.51R | +94 |
| 2025-10-11 | 5.791 | 4.218 (-27.2%) | 8.150 (+40.7%) | 2025-10-14 | time | +0.68R | +46 |
| 2025-11-12 | 7.821 | 6.375 (-18.5%) | 9.990 (+27.7%) | 2025-11-15 | time | -0.23R | -17 |
| 2025-11-21 | 6.124 | 5.285 (-13.7%) | 7.383 (+20.6%) | 2025-11-24 | time | +0.08R | +4 |
| 2026-01-31 | 3.825 | 3.441 (-10.0%) | 4.401 (+15.1%) | 2026-02-04 | time | +0.08R | +4 |
| 2026-02-06 | 3.168 | 2.716 (-14.3%) | 3.847 (+21.4%) | 2026-02-09 | time | +0.74R | +57 |
| 2026-02-11 | 3.494 | 2.971 (-15.0%) | 4.278 (+22.4%) | 2026-02-15 | time | +0.25R | +18 |
| 2026-08-14 | 3.201 | 2.962 (-7.5%) | 3.560 (+11.2%) | 2026-08-17 | time | +0.45R | +24 |
| 2026-10-08 | 7.327 | 6.716 (-8.3%) | 8.243 (+12.5%) | 2026-10-11 | time | +0.65R | +39 |
| 2026-11-14 | 5.126 | 4.571 (-10.8%) | 5.958 (+16.2%) | 2026-11-17 | time | +0.20R | +14 |
| 2026-11-20 | 4.354 | 3.910 (-10.2%) | 5.020 (+15.3%) | 2026-11-23 | take | +1.48R | +115 |

Outcome mix: 5 targets at **+1.48R** average, 13 time exits at **+0.37R**
(the clock closes whatever is left, including one small loss), 1 stop at
**-0.93R** (gapped through). Expectancy **+0.59R/trade**, i.e. ~$30 per trade
on a $1,000 wallet at 5% risk - but only **0.79 trades per month**. Median gap
between entries is 30 days and the longest was 6 months, so two weeks of paper
trading will usually contain **zero trades**; budget ~6 weeks for the first
fill and a year for a dozen.

## Trailing instead of the 1.5R target (tested, rejected)

The natural challenge is that 1.5R caps the upside. A Chandelier trail
(`trail_atr` ATRs under the highest high since entry) is the obvious
alternative: remove the target, let the stop follow the move, and hold until
the trail is hit. The engine now supports that plus an optional
`trail_after_r` delay; `uni_trail.py` runs 62 configurations on the same
window, costs, 5% risk and 50% cap (`uni_trail.csv`).

| Exit rule | Full | Max DD | Sharpe | Trades | Win | Test | Test DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| **1.5R target / 18 bars (shipped)** | **+67.82%** | **-5.27%** | **2.13** | 19 | 89.5% | +19.27% | -3.46% |
| No target / 18 bars | +55.21% | -7.09% | 1.75 | 19 | 89.5% | +17.11% | -3.46% |
| No target / 2.5 ATR trail / no time exit | +53.11% | -9.03% | 1.46 | 22 | 63.6% | +19.79% | -5.95% |
| No target / 3.0 ATR trail / 18 bars | +55.87% | -7.09% | 1.75 | 20 | 90.0% | +16.22% | -3.46% |
| No target / 2.5 ATR trail armed at 1R / no time exit | +57.07% | -16.22% | 1.20 | 20 | 70.0% | +16.74% | -13.51% |
| 1.5R target + 3.0 ATR trail / 18 bars | +67.93% | -5.12% | 2.14 | 20 | 90.0% | +19.27% | -3.46% |

Six-month folds (return / max DD):

| Exit rule | F1 | F2 | F3 | F4 |
|---|---:|---:|---:|---:|
| 1.5R / 18 bars | +24.6% / -5.3% | +10.0% / -3.4% | +5.6% / -3.5% | +12.9% / -2.3% |
| No target / 2.5 ATR trail / no time exit | +22.0% / -8.8% | +0.9% / -6.6% | **-0.3% / -5.3%** | +20.1% / -6.0% |
| No target / 2.5 ATR trail armed at 1R | +25.6% / -8.9% | +9.5% / -7.1% | +6.9% / -8.1% | +9.2% / -13.5% |

The arithmetic explains it. At the 1.5R target the peak is 3.75 ATR above
entry; a 2.5 ATR trail at that peak locks only 1.25 ATR = **0.5R**, not 1.5R.
To lock 1.5R with that trail, the peak must reach 6.25 ATR = **2.5R**. UNI's
median bounce does not get there; one trade did (+55.8% notional), but one
runner did not pay for the 21 smaller exits.

The best no-target trail made **+0.52pp** more than the fixed target in the
holdout (+19.79% vs +19.27%) but gave back **14.7pp** over the full window
(+53.11% vs +67.82%), with a drawdown nearly twice as deep (-9.03% vs
-5.27%), Sharpe 1.46 vs 2.13, CAGR 23.7% vs 29.6%, Calmar 2.63 vs 5.61,
and one negative six-month fold. Win rate fell from 89.5% to 63.6%; all 22
exits were stops. It did not ride a trend - it gave back the snapback.

Delaying the trail until 1R does not rescue it: it holds through the first
pullback, then the trail sits below entry and the drawdown goes to -16.22%.
Keeping the 1.5R target and adding a trail is a rounding difference
(+67.93% vs +67.82%), not a new edge, and adds one more exit to manage.

**Verdict: keep the 1.5R target for UNI.** This is a mean-reversion
capitulation bounce, not a trend continuation: 13 of 19 exits are the 18-bar
clock, and the five clean targets are the reliable part. Trailing belongs in
a trend sleeve; on this one it turns winners into scratches and holds losers
longer. The test-slice gain is inside the noise of seven trades and is not
worth the extra drawdown.

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

Promoted to **paper** on 2026-11-28:
`promotions/paper/capitulation_dip_uni.json`, params hash
`sha256:3520143553854675`, $1,000 wallet. The paper record is kept by the
forward runner (`bot forward`), not by `bot live`:

```bash
python -m bot forward --strategy capitulation_dip_uni --symbol UNI/USDT \
  --interval 4h --risk-per-trade 0.05 --max-position-pct 0.5 --poll 900
```

- It replays the sleeve through the same `Engine` as the backtest, so the
  2.5 ATR stop, the 1.5R target and the 18-bar time exit all fire; the
  deploy equity is pinned on the first cycle and state lives in
  `runs/paper/forward/status.json`.
- **`bot live` is not used for this sleeve.** It is stop-only on spot and
  resets the hold clock on restart, so it would silently run a different
  strategy. Its live-compatible variant was `rr=None`: +55.2%, -7.1% max DD,
  19 trades, Sharpe 1.75 at the same 5% risk / 50% position cap.
- **`bot auto` is not used either.** The 15-minute books are a multi-coin
  SMA/trail book hardcoded to SmaCross.

What to expect: 19 trades in two years is 0.79/month, so the paper record
will be flat most weeks and the first forward fill can take a month or more.
Judge it on the forward trades only, and do not read a 0-trade first month as
a failure of the rule.
- **A drawdown stop is a brake, not a floor.** It stops new entries after
  the account is down; it does not cap the loss at 25%. The slow-bleed test
  still lost ~27% before the switch.

So: do not treat a `bot live` run with the 1.5R target as a validation of
this backtest. Promotion to paper needs one code change first - teach the live
loop to persist and check the target (and the entry timestamp) the same way it
handles the stop - or it must be promoted as the `rr=None` variant with that
choice recorded in the manifest evidence (`deploy/PROMOTION.md`, exit parity).
After that: a paper sleeve that records simulated fills with the same exit
semantics, then a forward record long enough to cover several trades - at
0.79 trades/month, a dozen trades is about 15 months.

To watch the historical replay through the CLI:

    $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m bot paper --source binance `
      --symbol UNI/USDT --interval 4h --n-bars 4381 --strategy capitulation_dip_uni `
      --param window=20 --param drop_pct=0.20 --param stop_atr=2.5 `
      --param rr=1.5 --param max_hold=18 --risk-per-trade 0.05 `
      --max-position-pct 0.5 --max-drawdown-stop 0.25

## Reproduce

    # writes runs/trades.csv (the trade table below reads it)
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m bot backtest --source binance `
      --symbol UNI/USDT --interval 4h --start 2024-11-28 `
      --strategy capitulation_dip_uni --risk-per-trade 0.05 --max-position-pct 0.5 `
      --fee-bps 10 --slippage-bps 5

    $env:PYTHONPATH='src'; .venv\Scripts\python.exe research/uni/uni_trade_table.py
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe research/uni/uni_final.py
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe research/uni/uni_walkforward.py
    $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_capitulation_dip_uni -v

`uni_trade_table.py` rebuilds the per-trade price translation above;
`uni_final.py` prints the full, train, test, fold, risk, cost, trade and
bootstrap tables; `uni_walkforward.py` prints the 180-config grid summary.
