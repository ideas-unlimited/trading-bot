# WVFX-Stoch — tuning and timeframe fit

Question: can `wvfx_stoch` be tuned into a SOL strategy, and which timeframe does
it fit? Answer: **4h only**. Tuned 4h is +25.3% over two years on 15 trades while
SOL fell 70%; the same rules lose 15.2% out of sample on 6h and fire twice on 1d.

Window: **2024-11-28 → 2026-11-28** (the 2-year cap), Binance spot, long only,
$1,000, 3% risk per trade, 10 bp fee + 5 bp slippage per side.
4h = 4,380 bars, 6h = 2,920, 1d = 730. In-sample = first 60%
(→ 2026-02-09), out-of-sample = the remaining 40% (→ 2026-11-28).

## Verdict

| tf | trades | 2y return | IS | OOS | win | max DD | fit |
|----|--------|-----------|----|-----|-----|--------|-----|
| 4h | 15 | **+25.3%** | +17.9% | +6.3% | 47% | -9.2% | yes |
| 6h | 7 | -12.0% | +3.8% | -15.2% | 14% | -20.6% | no |
| 1d | 2 | -6.1% | -3.1% | -3.1% | 0% | -6.8% | no — sample too small |

SOL buy & hold over the same window: **-69.8%**. House defaults on 4h (2.0 ATR
stop, 2.0R, 40-bar hold) give +12.3% on 14 trades but only -0.2% out of sample.

## Config

```python
oversold=20, oversold_window=5, entry_window=1, trigger="cross",
regime_days=200, stop_atr=1.5, rr=2.5, max_hold=60, exit_cross=False,
band_mult=2.0, percentile_high=0.85, k_length=14, smooth_k=1, smooth_d=3
```

Only the exit geometry moved: 1.5 ATR stop, 2.5R target, 60-bar time exit. The
entry (WVF first green bar, %K under 20 within 5 bars, close above the 200-day
mean) is unchanged, and the stochastic smoothing stays at 1/3 — the user's 14/3/5
smoothing cuts the setup from 118 events to 33 on this sample.

## Method

Each config is run once, causally, over the whole window, then the equity curve and
trade list are split at 60%. Selection looks only at in-sample trade returns; the
out-of-sample half is reported, never selected on. Scores use the worse of the two
in-sample halves (`min(h1, h2)`) and require at least 5 trades, so a config has to
work in both halves of its own fit window before it is looked at.

Per-symbol numbers here are always one coin's own equity. The tuning grid itself is
scored on the pooled five majors (BTC, ETH, BNB, SOL, XRP) so that a cell cannot win
on one coin's luck — the per-symbol table then shows whether the winner travels.

## Cross-symbol check (identical parameters)

Out-of-sample return, same split date, no parameter change per coin:

| | 4h | 6h | 1d |
|---|---|---|---|
| BTC/USDT | +12.0% | +9.6% | -3.2% |
| ETH/USDT | +16.1% | -1.1% | -3.1% |
| BNB/USDT | -3.6% | +4.0% | +0.0% |
| SOL/USDT | +6.3% | -15.3% | -3.1% |
| XRP/USDT | +27.4% | -18.7% | +0.7% |

4h is 4/5 positive, 6h is 2/5, 1d has 10 trades across all five coins in two years.
The timeframe conclusion is not SOL-specific and not a knife edge: on 4h, **all 128**
exit geometries tested were positive out of sample (median +1.1% per trade, median
t-stat 1.88). The chosen cell is a plateau centre (in-sample +1.31%/trade on 21
trades, out-of-sample +1.62% on 34, t = 2.34, median full return +20.5%).

## Tuning path — 684 configurations on SOL

| stage | configs | IS positive | OOS positive |
|-------|---------|-------------|--------------|
| entry rarity / trigger / regime | 144 | 111 | 19 |
| exit geometry (stop x rr x hold) | 384 | 192 | 150 |
| WVF + stochastic parameters | 108 | 82 | 58 |
| trailing stop variants | 48 | **48** | **2** |

The trailing row is the reason this note exists. Every trailing variant was
positive in-sample and 46 of 48 lost out of sample (median -5.3%): a Chandelier
trail turns the fixed 2.5R target into a partial exit that gives back the bounce.
Anything selected on SOL alone is roughly a coin flip, which is why the parameters
come from the pooled grid and only the *reporting* is per coin.

SOL's own best cell used `regime_days=100` and scored +33.6% full / +3.8% OOS on 11
out-of-sample trades (t = 0.8). The config above scores +25.3% / +6.3% and is also
what four of the five majors pick. At ~7 trades a year, preferring the higher
in-sample number would be fitting the noise.

## RSI gates (from the pasted indicator)

`require_rsi_flip=True` leaves **0 trades** on SOL with the house defaults and 1
trade with the tuned config; `exit_rsi_overbought` changes nothing. The RSI
oversold flip and the WVF panic bar are the same event on a 4h chart, so the gate
only subtracts samples. Both stay off by default and are documented as tested.

## Against momentum_swing (SOL's current candidate)

`momentum_swing` 6h (lookback 40, 200-day regime, 2.5 ATR stop, 3.5 ATR trail) on
the same split: +41.2% full, +18.7% IS, **+18.9% OOS**, 26 trades, 42% win,
-13.5% max DD. Caveat: those parameters came from an earlier full-window sweep, so
the out-of-sample figure is an upper bound rather than a clean holdout. WVFX's
+6.3% is clean. A fair head-to-head needs momentum_swing re-selected on the first
60% only — open item.

## Honest limits

- 34 out-of-sample trades pooled, 10 of them on SOL. The edge is ~+1.6% equity per
  trade; t = 2.3 pooled is suggestive, not settled, and SOL alone is inside noise.
- One market regime: a long SOL bear with two violent bounces. A grind lower with
  no capitulation would produce very few entries, and the strategy would sit in
  cash — which is the design, but it is untested against a slow bleed.
- The five majors are correlated; five positive cells in one crash are not five
  independent proofs.
- The 1d result is not evidence of anything — two trades cannot refute or confirm
  a strategy, which is exactly why 1d is rejected as *unusable*, not as *wrong*.

## Files

- `research/wvfx_tune.py` — staged SOL tuner (writes `wvfx_stage1..4.csv`)
- `research/wvfx_pooled.py` — pooled 4h exit map over five majors (`wvfx_pooled_4h.csv`)
- `research/wvfx_xcheck.py` — one config, per symbol, IS/OOS/full (`wvfx_xcheck.csv`)
- `research/wvfx_report.py` + `wvfx_report_template.html` — build `wvfx_report.html`
## Trailing exits vs the hard target (added 2026-11-28)

The house preference is to trail rather than cap: `README.md` records the
Chandelier trail as the largest single improvement on the gated 4h book, and
`momentum_swing` ships with no hard target at all. WVFX was tested against the
same preference on the tuned entries, pooled over the five majors at 4h
(`research/wvfx_trailfit.py`, `wvfx_trailfit.csv`):

| variant | IS tr | IS /trade | OOS tr | OOS /trade | t | med full | coins + | avg hold |
|---|---|---|---|---|---|---|---|---|
| trail 7.0, no target | 21 | -0.29% | 31 | **+3.20%** | 2.50 | +16.6% | 5/5 | 4.9 d |
| trail 5.0, no target | 21 | -0.29% | 31 | +1.92% | 2.25 | +12.3% | 5/5 | 4.4 d |
| **2.5R target, no trail** | 21 | +1.33% | 34 | +1.63% | 2.36 | **+20.5%** | 4/5 | 2.8 d |
| trail 3.5, no target | 22 | -0.35% | 33 | +1.19% | 1.60 | +4.1% | 3/5 | 3.1 d |
| trail 3.5 + target | 22 | +0.66% | 34 | +0.98% | 1.49 | +15.6% | 3/5 | 2.3 d |
| trail 1.5, no target | 23 | +0.22% | 36 | +0.39% | 1.13 | -1.2% | 2/5 | 1.1 d |
| trail 2.5, no target | 23 | +0.08% | 34 | -0.03% | -0.07 | +1.2% | 3/5 | 1.8 d |

Two different things are being measured. A **tight** trail (1.5-3.5 ATR) makes
the bounce exit early and gives the edge back - that is the 48-variant result in
the stage-4 table above, 2 of 48 positive out of sample. A **wide** trail with no
target (5-7 ATR) is nearly inert as a trail and mostly just *uncaps* the winner:
it raises the average out-of-sample trade (+3.20% vs +1.63%) and is positive on
all five coins, but keeps the money in the market ~75% longer and finishes with a
lower account return (+16.6% median vs +20.5%) and a negative in-sample half.
Under the select-on-IS rule the fixed target wins; the uncapped variant is a
genuine trade-off, not a free lunch.

Verdict: keep the 2.5R target in the WVFX sleeve. The preference for trailing is
right where it was measured - trend continuation (`momentum_swing`, the gated
book) - and WVFX is mean reversion on a panic spike, which is the case where a
target earns more than patience.