# JUP: next 15m demand zone as target, then trail

Research only. Follow-up to JUP_LIQUIDITY.md (4h/6h/1d DCA zones, fixed ATR/1.5R exits).

## Window and rules

- Binance spot JUP/USDT, long only, one position at a time.
- 4h decision bars: 2025-11-28 08:00 -> 2026-11-27 20:00 UTC (2,188 bars).
- 15m execution bars: 2025-11-28 05:45 -> 2026-11-28 05:15 UTC (last 12 months only, sub-4h rule).
- Signals on completed 4h bars; first possible fill is a later 15m bar, never the signal bar.
- Costs: 10 bps fee + 5 bps slippage per side. A fresh fill cannot also hit the stop, target or trail on that bar; exits are checked from the next 15m bar. For an existing position the stop is checked before the target, so a bar that touches both is booked as a loss.
- Demand zones from confirmed swing lows (5/5 pivots), half-width 0.5 x ATR(14), using only zones whose confirmation is complete by the signal close. The prefix test below proves this is causal.
- Entry clusters: 4h/6h/1d bands; three equal DCA tranches at cluster upper / center / lower; stop at lower - 1.0 x ATR(14) on 4h. Entry orders expire after 72h.
- Target: nearest confirmed 15m demand band above the 4h signal close (not above the eventual fill), lower edge > signal close + 0.2% and > entry upper + 0.2%. Target is the band center; the lower edge is also tested.
- Exit modes:
  - hard: sell 100% at the first 15m band.
  - partial: sell 50% at the band, trail the rest.
  - trail_after: sell nothing at the band; arm a 1.5 x ATR(15m) Chandelier trail and exit on it.
  - trail_only: trail from entry, no target. This is the control.
- Hard stop stays at lower - 1 ATR4 in every mode. Time stop: 120h (30 x 4h bars) after first fill.

## Why "next 15m zone" is not a fixed 2-3% target

For the 892 signals that had an available 15m target:

| measure (target) | p10 | p25 | median | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| distance above entry upper, lower edge | 1.58% | 2.84% | 5.59% | 8.71% | 12.16% |
| distance above entry upper, center | 1.80% | 3.11% | 5.93% | 9.33% | 12.70% |
| reward/risk, lower edge | 0.30 | 0.53 | 0.96 | 1.39 | 1.94 |
| reward/risk, center | 0.37 | 0.60 | 1.03 | 1.46 | 2.01 |

The next 15m band is typically 5-6% above the DCA zone, but it is only about 1R away because the structural stop is also 2-3% away. About half the targets are below 1R. That is why a hard 2-3% take-profit is structurally poor here: it cuts a ~1R target down to 0.5R or less. The user's .210/.205/.200 -> .230 sample was a ~9.5% move from .210 and ~12.2% from the .205 average; that is a wide target, not a 2-3% bounce.

## Result: same entries, 2+ timeframes, three DCA tranches

All rows use 1.5 x ATR(15m) trail where applicable, target center, 120h hold.

| exit | trades | win | PF | total R | avg R | 2% risk* | max DD* |
|---|---:|---:|---:|---:|---:|---:|---:|
| hard target at center | 65 | 67.7% | 1.50 | +17.3 | +0.27 | +39.3% | -9.4% |
| 50% at center + trail | 64 | 67.2% | 1.54 | +18.2 | +0.28 | +41.7% | -9.8% |
| trail after center, no sale | 64 | 65.6% | 1.58 | +19.3 | +0.30 | +44.6% | -10.2% |
| trail after lower edge, no sale | 67 | 65.7% | 1.55 | +18.2 | +0.27 | +41.8% | -9.6% |
| trail from entry, no target | 123 | 18.7% | 0.27 | -9.0 | -0.07 | negative | negative |

*Risk-normalised approximation: each trade risks 2% of current equity to the stop; equity compounds trade by trade. It is not a live position-size simulation and does not cap notional at 100% of cash.

The important sign is the control: trailing from entry without waiting for the target loses -9.0R. The target is doing real work as a filter; "trail immediately" is not the edge.

## Confluence: 1d does not help

| confluence | trades | win | PF | total R | avg R | top-3 share | drop top 3 | 2% risk | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| at least 2 TF | 64 | 65.6% | 1.58 | +19.3 | +0.30 | 61% | +11.5 | +44.6% | -10.2% |
| exactly 2 TF | 55 | 67.3% | 1.50 | +16.6 | +0.30 | 80% | +8.9 | +37.5% | -10.0% |
| 4h + 6h only | 54 | 66.7% | 1.37 | +13.1 | +0.24 | 112% | +5.3 | +28.1% | -7.8% |
| exactly 3 TF | 16 | 31.2% | 0.71 | -2.6 | -0.16 | - | - | -5.3% | -5.5% |
| exactly 1 TF | 17 | 76.5% | 2.46 | +6.4 | +0.38 | 102% | +1.6 | +13.4% | -4.2% |

The original "all three timeframes = strongest" intuition is rejected on this sample. Exact 3 TF lost money; exact 2 TF was the best per-trade bucket (+0.30R, +16.6R) and is the one I would forward. "At least 2" shows a higher total (+19.3R) because of one-position path effects, but its 10 all-3 trades summed -0.29R, so there is no reason to require the third. 4h and 6h are correlated, so this is really one confirmation, not two independent ones. The 1d band adds conflict, not edge.

## Cost stress (2+ TF, trail after center, 1.5 ATR15)

| fee/slip per side | trades | win | PF | total R | top-3 share | 2% risk | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 / 0 | 64 | 67.2% | 1.78 | +23.8 | 50% | +58.1% | -8.2% |
| 10 / 5 bps | 64 | 65.6% | 1.58 | +19.3 | 61% | +44.6% | -10.2% |
| 20 / 10 bps | 64 | 62.5% | 1.40 | +14.9 | 81% | +32.5% | -12.1% |
| 30 / 15 bps | 64 | 62.5% | 1.24 | +10.5 | 125% | +21.7% | -13.9% |

At 30/15 bps the three best trades account for more than the total P&L; the rest of the book is negative. The 10/5 result is the only one with a comfortable cushion.

## Stability and sensitivity

Unless a bullet says otherwise, the sensitivity numbers below use the 2+ TF book; the exact-2 fold is called out first because that is the recommended entry.

- Exact 2 TF half-splits: first half (through 2026-05-27) 22 trades, 59.1% win, PF 0.99, +2.5R; second half 33 trades, 72.7% win, PF 2.03, +14.1R. The leading 2+ TF book was 26/38 trades, 57.7%/71.1% win, PF 1.16/1.99, +4.1R/+15.1R. December-March were negative to flat; October-November supplied +9.4R.
- Trail multiplier, target center: 1.0 ATR +18.2R, 1.5 +19.3R, 2.0 +17.1R, 2.5 +15.7R. Plateau over 1.0-2.0, no knife edge.
- Stop, target center: 0.75 ATR4 +15.5R, 1.0 +19.3R, 1.25 +14.9R, 1.5 +9.1R. This one is a peak at 1.0; a tighter or wider stop gives up 4-10R.
- 15m target band age: 1d +14.6R, 3d +19.3R, 5d +18.8R, 7d +17.1R. Three to five days is a plateau; very fresh bands are worse.
- 15m band width: 0.5 ATR +19.3R, 0.75 ATR +19.2R, 1.0 ATR +20.1R. Not sensitive.
- DCA tranches: 1 +9.9R, 2 +15.7R, 3 +19.3R. DCA adds, but a single tranche is still positive; the result does not depend on all three filling.
- Time stop: 60h +19.1R, 120h +19.3R, 180h +20.1R, 240h +21.4R. Longer is slightly better; 120h is the base to avoid holding through more regime.
- Minimum target RR filter: hard target totalR falls from +15.8R (all) to +7.4R at RR >= 0.75, +7.1R at >= 1.0, +6.6R at >= 1.5 and +5.9R at >= 2.0. Do not filter out low-RR targets; the big runners are often the ones whose first band looked close.
- Exact 3 TF: -2.6R with 16 trades. It is not a small-sample positive that needs more time; the sign is wrong and the win rate is 31%.

## Probability arithmetic

For the leading 2+ TF trail-after-center run:

- 64 trades, 42 winners, 22 losers.
- Average win +0.97R, average loss -0.97R.
- Breakeven win rate at that payoff: 50.0%.
- Actual win rate: 65.6% -> expectancy +0.30R per trade before position sizing.
- Bootstrap 95% interval for expectancy: +0.035R to +0.573R. The lower bound is barely above zero, so this is a positive sample, not a proven edge.
- Wilson 95% interval for win rate: 53.4% to 76.1%. Wide.
- Drop the best 3 trades: +11.5R, average +0.19R. Drop the best 5: +6.8R, average +0.12R. The edge survives, but it is right-tail dependent.
- Max 3 consecutive losses. At 2% risk the modelled max drawdown is -10.2%.

## No-lookahead prefix test

Rebuild all data and zones truncated at 2026-03-01, 2026-06-01 and 2026-09-01, then compare trades that closed before the cut with the full run:

| cut | full early trades | truncated trades | identical | max P&L diff |
|---|---:|---:|---|---:|
| 2026-03-01 | 19 | 19 | yes | 0.0 |
| 2026-06-01 | 28 | 28 | yes | 0.0 |
| 2026-09-01 | 41 | 41 | yes | 0.0 |

A zone is only usable after its pivot confirms and one timeframe duration passes; the target list at each signal uses only zones already available. The prefix result confirms no future bars leak into earlier trades.

## Verdict

This is more adaptive than the fixed 2-3% TP idea, but it is still a one-year, one-symbol result with a weak first half-year and a top-3 concentration of 80% in the exactly-2 bucket. The ATR/1.5R study used a different window and exit, so the two are not directly comparable. Do not promote it yet.

What the numbers support:

1. Use the next confirmed 15m demand band above the signal as an adaptive first target. Do not use a fixed 2-3% TP; the median next band is ~5.9% above the DCA zone and ~1R away, so a 2-3% target is less than 0.5R.
2. Use exactly two timeframes (any two), not three. Exact 2 TF: +16.6R, PF 1.50, 2% risk +37.5%, max DD -10.0%. Exact 3 TF: -2.6R. In the 2+ book the 10 all-3 trades summed -0.29R. 4h+6h is the core pair; 1d+4h/6h also qualify as two but are only a small minority of the trades. Do not force all three.
3. Use three DCA tranches. Three beats one by ~9R; two is in between.
4. Keep the structural stop at lower - 1 ATR4. This is a sensitivity peak; do not tighten it to "improve" RR without re-testing.
5. Take 50% at the 15m band center, then trail the rest at 1.5 ATR15. This is the practical version of "TP then trail": on exact 2 TF it made +15.4R, PF 1.45, 2% risk +34.4%, max DD -9.6%. The no-sale trail on exact 2 TF made +16.6R, +37.5%, max DD -10.0%; it gives back more but has the better total.
6. If the trail cannot be automated, the hard target at the band center is still positive: on exact 2 TF, +14.4R, PF 1.40, 2% risk +31.7%, max DD -9.3%.
7. Never trail from entry. That book loses -9.0R; the target filter is the edge.

Do not deploy on this evidence. Dry-run the exact rule on demo for at least 20-30 trades, then re-check win rate and average R. Also test the same rule on another coin before treating it as a JUP sleeve; one year of one symbol with 55 trades is not enough for capital.

Reproduce:

    $env:PYTHONPATH='src;.'
    .venv\Scripts\python.exe research/jup/jup_15m_target.py

Selected rows are in research/jup/jup_15m_target.csv. Prior 4h/6h/1d ATR results are in JUP_LIQUIDITY.md; the two studies use different exit rules and must not be compared directly.
