# JUP/USDT liquidity-zone bounce study

**Window:** 2024-11-28 08:00 → 2026-11-27 20:00 UTC, Binance spot, 4h decision
bars (4,378 bars). Buy & hold over the same window: **-77.3%** (year 1
-77.3%, year 2 -1.2%). This is a hostile long-only sample, so every result
below is net of costs and should be read against that tape, not against a
bull-market baseline.

Research only: no production sleeve, registry entry or test was added.

Follow-up with a different exit (next 15m demand zone as target, then trail):
[JUP_15M_TARGET.md](JUP_15M_TARGET.md). Do not mix its one-year numbers with this two-year table.

## What was tested

- Demand bands around **confirmed swing lows** on 4h / 6h / 1d (5-bar pivot),
  half-width 0.5 × ATR(14), 60-day life. A band becomes usable only after the
  pivot confirmation bar closes plus one bar of its own timeframe.
- A **cluster** is the intersection of overlapping bands; its confluence is
  the number of distinct timeframes in it.
- Passive entry: resting buy limits at the cluster's upper / centre / lower
  levels. With 3 tranches the later limits can still fill after the first
  one, so this is true DCA rather than three fills on one bar.
- Reclaim entry (tested as a control): a 4h bar pierces the lower band and
  closes back above the cluster centre; buy the next open, then add at
  centre / lower.
- Stop = cluster lower − 1.0 × ATR4. Target = average entry + 1.5R. Time
  exit after 30 × 4h bars. Total risk 5% of current equity, position cap 50%.
- Costs: **10 bps fee + 5 bps slippage per side**. Signals on completed
  bars, fills on later bars. No same-bar target after a fresh fill; a
  same-bar stop is allowed and conservative.
- Causality check: truncating all history at the year-1/year-2 split leaves
  the 35 pre-split closed trades and their equity path identical.

## Headline result: confluence is not monotonic

Exact bucket means *exactly* N timeframes in the cluster (not “at least N”).

| config | net return | max DD | Sharpe | trades | win | avg trade | PF | fills | year 1 | year 2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| exact 1 TF, 1 tranche | -30.9% | -46.0% | -0.74 | 63 | 36.5% | -0.90% | 0.72 | 1.00 | -34.7% | +5.8% |
| exact 1 TF, 3 tranches | -28.4% | -34.5% | -1.13 | 64 | 39.1% | -0.10% | 0.61 | 2.34 | -27.2% | -1.6% |
| exact 2 TF, 1 tranche | -2.0% | -31.8% | 0.05 | 73 | 46.6% | +0.11% | 0.98 | 1.00 | -18.4% | +20.1% |
| exact 2 TF, 3 tranches | -10.4% | -26.9% | -0.32 | 77 | 48.1% | +0.89% | 0.87 | 2.40 | -12.1% | +1.9% |
| exact 3 TF, 1 tranche | +3.2% | -12.1% | 0.18 | 32 | 40.6% | +0.33% | 1.06 | 1.00 | +9.5% | -5.7% |
| exact 3 TF, 3 tranches | -6.3% | -8.3% | -0.34 | 32 | 43.8% | +0.61% | 0.82 | 2.38 | -2.5% | -3.9% |

One timeframe is a knife-catcher. Two is roughly flat. Three is slightly
positive but has only 32 trades and is negative in year 2. **The claim that
all three timeframes make the strongest setup is not supported by this
sample.**

If instead the bot always takes the highest-confluence cluster available
(rather than an exact bucket), the numbers change because the ranking itself
avoids most single-TF setups:

| config | net return | max DD | Sharpe | trades | win | avg trade | PF | fills | year 1 | year 2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| best ≥1 TF, 1 tranche | +18.4% | -30.9% | 0.46 | 101 | 45.5% | +0.54% | 1.10 | 1.00 | -13.2% | +36.3% |
| best ≥1 TF, 3 tranches | -15.9% | -31.4% | -0.44 | 104 | 46.2% | +1.01% | 0.85 | 2.31 | -19.6% | +4.6% |
| **best ≥2 TF, 1 tranche** | **+35.8%** | **-12.2%** | **0.81** | **85** | **49.4%** | **+0.89%** | **1.23** | **1.00** | **+3.9%** | **+30.7%** |
| best ≥2 TF, 3 tranches | -9.0% | -21.1% | -0.25 | 89 | 49.4% | +1.23% | 0.90 | 2.43 | -14.0% | +5.7% |

The +35.8% line is the best number in the study, but it is **not** evidence
that 3-TF zones are strongest: exact 3 TF is only +3.2%. The gain comes from
preferring 2/3-TF clusters over 1-TF clusters, and from path-dependent
selection between the two buckets. Exact 2 TF alone is -2.0%, so the
combination is not additive or stable.

## Why the “2-TF” result is weaker than it looks

- 53 of the 85 best-≥2 trades are **4h + 6h only**; 27 are all three; the
  other 5 are 1d + one lower timeframe. 4h and 6h are built from the same
  price path and usually confirm the same swing, so they are correlated
  confirmation, not two independent votes.
- Requiring 1d as one of the two timeframes leaves only 32 trades; their
  per-trade average (+0.92%) is essentially the same as the 4h+6h-only
  average (+0.87%). Adding the daily does not improve the trade.
- Exact 3 TF is rare (32 trades) and its year 2 is negative. By the time all
  three bands overlap, the level is obvious and often already tested.

## DCA: it fills, but it does not help

With three tranches the simulator really does fill later: average fills are
**2.34-2.43**, and 56 of 89 best-≥2 DCA trades filled all three. It is not a
case of the lower limits never being reached.

| bucket | 1 tranche | 3 tranches | change |
|---|---:|---:|---:|
| exact 1 TF | -30.9% / -46.0% DD | -28.4% / -34.5% DD | slightly less bad, still a loser |
| exact 2 TF | -2.0% / -31.8% DD | -10.4% / -26.9% DD | lower return, lower DD |
| exact 3 TF | +3.2% / -12.1% DD | -6.3% / -8.3% DD | DCA turns it negative |
| best ≥2 TF | +35.8% / -12.2% DD | -9.0% / -21.1% DD | much worse |

The pattern is consistent: averaging down wins a few more trades (48.1% vs
46.6% in exact 2 TF) but the profit factor falls below 1 (0.87 vs 0.98),
because the third tranche is largest exactly when the zone is failing. In a
falling market, DCA converts a small stopped-out probe into a full-size loss.

## Pierce-and-reclaim control

Waiting for a 4h close back above the cluster centre after a pierce did not
fix it:

| config | net return | max DD | trades | year 1 | year 2 |
|---|---:|---:|---:|---:|---:|
| reclaim centre, 1 TF | +35.0% | -17.6% | 55 | +34.3% | +0.5% |
| reclaim centre, 2 TF | +1.4% | -20.7% | 40 | +10.8% | -8.5% |
| reclaim centre, 3 TF | -0.8% | -13.5% | 15 | +4.1% | -4.6% |

The 1-TF reclaim is the only line with a real year 1, but it goes flat in
year 2 and has no confluence edge. More timeframes make it worse.

## Robustness checks

Six-month folds for the best-≥2 single-entry line:

`-1.0% / +5.0% / +15.5% / +13.0%` (trades 14 / 21 / 24 / 26, DD -12.2% /
-8.4% / -10.4% / -7.2%). That is 3 of 4 positive and the drawdown is stable,
which is the strongest point in favour of the line.

But the parameter neighbourhood is a knife edge, not a plateau:

| knob | values and net return |
|---|---|
| swing length | 3: -25.4%, 4: +29.3%, **5: +35.8%**, 6: -7.0%, 8: +28.4% |
| band half-width | 0.30: -2.7%, 0.40: +6.5%, **0.50: +35.8%**, 0.65: -25.0%, 0.80: -9.7% |
| max zone age | 30d: +43.3% (year 1 -0.3%), 45d: +29.9% (year 1 -7.5%), **60d: +35.8%**, 75d: +19.4%, 90d: +6.7% |
| stop / target | 1.0 ATR / 1.5R is a spike, not a plateau: 1.0/1.625 has year 1 -18.4%, 1.25/1.5 has year 1 -26.1%; only far-1.75R variants recover, and they are negative in year 1 |

Cost stress on best-≥2 single entry:

| fee / slip per side | net return | max DD |
|---|---:|---:|
| 0 / 0 | +47.4% | -11.6% |
| 10 / 5 bps (base) | +35.8% | -12.2% |
| 20 / 10 bps | +22.4% | -13.2% |
| 30 / 15 bps | +1.9% | -21.6% |

Best-≥2 DCA is negative even at **zero costs** (-0.7% gross), so its failure
is structural, not a cost artefact.

## Verdict

Do **not** ship this as a JUP sleeve. The honest reading of the data:

1. **One timeframe is not a strategy**; it loses badly.
2. **Two/three timeframes help only through ranking** — prefer higher
   confluence, skip single-TF zones — not because 3 TF is intrinsically
   strongest. Exact 3 TF is only +3.2% and is negative in year 2.
3. **DCA into the zone is the wrong shape for a falling knife.** It fills,
   but the extra size arrives when the zone is failing. It reduces return in
   every bucket and turns exact 3 TF negative.
4. The best line (+35.8%, -12.2% DD) is a **single-entry, 2-TF ranking**
   result with 85 trades over two years. It is parameter-fragile, mostly
   4h+6h correlated confirmation, and year 1 is only +3.9%. That is not
   enough to promote.

## If this idea is pursued

- Require **independent** confirmation: 1d plus one lower timeframe, and
  measure them separately rather than counting 4h+6h as two votes.
- Replace averaging-down with **confirmation + add-on-strength** (enter on
  the reclaim, add only after the bounce proves itself) and compare it
  honestly with a single entry.
- Add a market-regime gate (e.g. BTC/ETH trend or breadth) and test it on
  more than one coin; JUP alone, in a -77% two-year bear, cannot distinguish
  an edge from a survivorship-shaped bounce.
- Forward-paper a lab-only version before any promotion.

## Reproduce

```powershell
$env:PYTHONPATH='src'; .venv\Scripts\python.exe research\jup\jup_liquidity.py
```

Output: `research/jup/jup_liquidity.csv` (13 rows: exact 1/2/3 TF ×
1/3 tranches, best-available ≥1/≥2, and the three reclaim-centre controls).
