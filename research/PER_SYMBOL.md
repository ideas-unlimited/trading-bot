# Per-symbol strategy tuning: what the data says

> Historical study: measured on the old three-year cap. New backtests use the
> two-year cap; the numbers below are retained for comparison.

**Question:** should each symbol get its own tuned strategy instead of one
group strategy?

**Short answer:** the premise is right, but the obvious implementation —
pick the best parameter set per symbol on the last two years — does not
survive out of sample. A *fixed small set of sleeves per symbol* is the
version that held up better. The symbol should choose the strategy through
its own price action, not through a parameter grid fitted to its past.

Data: Binance 4h, `2023-11-26` → `2026-11-26` (3-year cap), BTC/ETH/BNB/
SOL/XRP. Train `2023-11-26` → `2025-11-26`; untouched OOS `2025-11-26` →
`2026-11-26`. Long-only spot, zero fees (brief), 2% risk per sleeve, 50%
sleeve kill-switch (25% would have halted some trend sleeves and created
fake flat OOS results), 4h bars/year = 2190.

## 1. The per-symbol parameter search

55 candidate configs per symbol across the existing families:

| family | configs | grid |
|---|---:|---|
| WVF stochastic | 12 | trigger cross/state × rr 1.5/2/2.5 × (stop 2/hold 40, stop 2.5/hold 50) |
| momentum swing | 12 | lookback 40/60/90 × ATR 2/2.5 × trail 3/3.5 |
| Donchian | 8 | 20/10, 55/10, 55/20, 100/20 × ATR 2/3 |
| RSI mean-reversion | 6 | entry 25/30/35 × exit 55/65 |
| volume climax | 9 | z 1.5/2/2.5 × drop 4/6/8% |
| SMA cross | 8 | 20/50, 30/50, 50/100, 20/100 × ATR 2/2.5 |

Selection rules:

* naive: highest train expectancy per trade, at least 6 train trades, train DD better than -30%;
* robust: family chosen first, config must be profitable in **both** train years, at least 6 train trades, win rate ≥ 30%, profit factor ≥ 1.2, train DD better than -25%.

### Result

| selector | mean OOS | median | positive symbols |
|---|---:|---:|---:|
| naive per-symbol best | **+3.9%** | +2.0% | 5/5 |
| robust per-symbol best | **-0.1%** | +1.7% | 3/5 |
| shared WVF default on each symbol | **+8.6%** | +10.3% | 4/5 |

The shared default WVF does not need a per-symbol parameter choice and
still beat both selectors on the average. The robust picks:

| symbol | picked on train | train n | OOS n | OOS return | OOS DD | OOS Sharpe |
|---|---|---:|---:|---:|---:|---:|
| BTC | mom lb40 ATR2.5 trail3 | 69 | 41 | -8.8% | -16.5% | -0.55 |
| ETH | mom lb60 ATR2 trail3.5 | 50 | 33 | +4.9% | -15.8% | +0.33 |
| BNB | mom lb90 ATR2.5 trail3 | 81 | 40 | +4.4% | -14.6% | +0.35 |
| SOL | WVF state rr2.5 stop2 h40 | 12 | 11 | +1.7% | -10.7% | +0.23 |
| XRP | Donchian 20/10 ATR2 | 62 | 34 | -2.5% | -25.8% | +0.02 |

Default WVF OOS for comparison: BTC +10.3%, ETH +14.1%, BNB -1.9%,
SOL +5.7%, XRP +14.8%. The only symbol where the robust selector added
value was BNB (+4.4% vs -1.9%); on BTC it cost 19 percentage points.

### Why it failed: train performance has no rank persistence

Pooled Spearman correlation between train Sharpe and OOS return across all
275 runs: **-0.151** (train expectancy: -0.094; train return: -0.131).
Per symbol:

| symbol | rank corr train Sharpe → OOS |
|---|---:|
| BTC | **-0.531** |
| ETH | -0.277 |
| BNB | -0.054 |
| SOL | +0.238 |
| XRP | +0.025 |

On BTC the best in-sample configs were actively *worse* out of sample.
Taking the top 10 train-Sharpe configs per symbol and averaging their OOS
returns gives: BTC -9.7% vs +0.1% for all configs; ETH +2.8% vs +4.8%;
BNB -2.8% vs -1.9%; XRP +4.4% vs +6.8%; only SOL improved (+6.8% vs
+6.1%). This is what selection noise looks like.

Symbol identity explains only **14.2%** of the variance of OOS returns;
strategy family explains **9.1%**. Most of the spread is config × period
noise. Mean OOS by family across all configs: WVF +6.94%, SMA +5.23%,
momentum +3.76%, volume climax +0.70%, RSI +0.57%, Donchian -0.61%.

## 2. Per-symbol allocation also overfits

Instead of tuning parameters, tune the **weights** across three fixed
sleeves (WVF, SMA 30/50 ATR2, momentum lb40 ATR2.5 trail3). 57 weight
triplets, selected on train Sharpe with a -25% DD cap:

* per-symbol selected weights: mean OOS **+6.47%**;
* equal weight 1/3 each: mean OOS **+10.58%**.

The optimizer chose 80% momentum for BTC and lost -6.4% OOS; equal weight
made +10.0%. It chose 80% momentum for ETH and made +9.2%; equal weight
made +17.1%. Again, the fit did not transfer.

## 3. What did hold up: fixed sleeves per symbol

$1,000 total, equal cash per symbol × sleeve, no rebalancing, zero fees,
2% risk per sleeve. Each sleeve is an independent wallet; combined equity
is the sum, so there is no leverage.

| book | full 3y return | full DD | full Sharpe | OOS return | OOS DD | OOS Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| WVF only (5 × $200) | +12.0% | -2.9% | 1.15 | **+8.6%** | -2.5% | 1.72 |
| WVF + SMA 30/50 (10 × $100) | **+57.6%** | -8.1% | 1.43 | **+14.9%** | -5.7% | 1.29 |
| WVF + SMA + momentum (15 × $66.67) | +54.0% | -7.1% | 1.46 | **+10.8%** | -6.2% | 1.01 |

Per-symbol OOS (each symbol as its own $1,000 book):

| symbol | WVF+SMA | 3-sleeve |
|---|---:|---:|
| BTC | +24.1% | +10.0% |
| ETH | +25.0% | +17.1% |
| BNB | -3.4% | +1.4% |
| SOL | +11.7% | +9.6% |
| XRP | +16.5% | +14.8% |

WVF+SMA is the return-max variant (4/5 positive); the 3-sleeve is the
robustness variant (5/5 positive, lower tail). Both beat WVF-only on
return, at the cost of a deeper drawdown. WVF-only has the best OOS
Sharpe because it is in the market less.

Consistency over six half-year windows (mean across symbols, 30
symbol-half-years): WVF+SMA was positive in all six means and 21/30
symbol-half-years; the 3-sleeve in all six means and 24/30; WVF-only in
14/30.

## 4. Interpretation

* **Different symbols do behave differently.** BNB is the clear WVF
  exception (WVF -1.9% OOS); SOL rewarded SMA/Donchian, XRP rewarded
  momentum/WVF, and BTC was mixed (WVF +10.3%, momentum -8.8%). That
  supports the user's instinct at the *family* level.
* **But 2 years is not enough to fit parameters.** The per-symbol best
  config is mostly a story about which regime happened in the training
  window. In 2026 that regime changed, and the low-frequency WVF default
  beat the fitted high-expectancy configs.
* **The symbol-specific part is state, not parameters.** A trend sleeve
  and a mean-reversion sleeve let the symbol decide: when it trends, the
  trend sleeve is in; when it shakes out and reclaims, WVF is in. That is
  more robust than a human picking "SOL = Donchian 100/20".
* **Do not compare these numbers to the earlier +45% WVF headline.** That
  was $1,000 concentrated in up to 3 positions at 1.5% risk. This is
  $1,000 split across 5 symbols × 2–3 sleeves at 2% risk per sleeve; lower
  return, much lower drawdown, more diversification.

## 5. Recommendation

1. **Do not hard-code per-symbol parameter sets.** The evidence is
   negative, especially on BTC.
2. **Use a fixed per-symbol book.** Start with 3 sleeves (WVF + SMA 30/50
   + momentum lb40) because it was the only all-positive variant, or
   WVF+SMA if return is preferred and BNB's -3.4% is acceptable.
3. **Risk:** $1,000 total, equal cash per sleeve, 2% risk per sleeve, 50%
   sleeve kill-switch, no leverage. Rebalancing quarterly or not at all;
   do not optimize weights on 2 years.
4. **Validate forward before calling it a strategy:** pre-register the
   mix, run a 3-fold walk-forward (6-month OOS segments), then demo for
   at least 3 months / 20 trades per sleeve. The mix above was chosen
   after seeing the 2026 OOS, so it is a hypothesis, not proof.
5. If BNB remains the weak sleeve, test replacing its trend sleeve with
   momentum as a **family prior** (not a parameter fit), and confirm on
   forward data.

## 6. Caveats

* One OOS year only, and it was a strong trend year. WVF's 2026 OOS is
  partly regime luck: its train returns on BTC/ETH/BNB were slightly
  negative.
* 4h only. 1d has too few per-symbol trades for this kind of selection;
  earlier 1d WVF work was weak. Sub-4h is capped at 12 months and is not
  used here.
* Zero fees per the brief. At 15 bps/side the trend sleeves lose more than
  WVF because they trade more; rerun with costs before sizing up.
* **The 70% conviction requirement is not solved by per-symbol tuning.**
  The per-symbol OOS win rates here were 20–60%; no config produced a
  genuine 70% per-trade predictor. The ensemble improves consistency, not
  certainty. If 70% is mandatory, the earlier `rr=0.5` tight-target
  variant is the only one that showed ~76% win rate, and that is a target
  artifact, not a stronger forecast.
* Demo only, long-only spot. No short/margin path.

## 7. Reproduce

```powershell
cd C:\Git\trading-bot

# 55 configs x 5 symbols: per-symbol selection + rank-persistence evidence
.venv\Scripts\python.exe -u research/per_symbol.py

# fixed-sleeve $1,000 portfolio and per-symbol sleeve OOS
.venv\Scripts\python.exe -u research/ensemble_book.py
```

Raw rows: `runs/per_symbol_4h.csv` (275 rows; train, H1/H2, OOS metrics).
