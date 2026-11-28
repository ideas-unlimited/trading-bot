# Five new long-only spot sleeves — research shortlist

**Date:** 2026-11-28  
**Data:** Binance spot, 4h and 1d, 2024-11-28 → 2026-11-28 (2 years)  
**Execution:** signal on completed bar close, fill at next bar open  
**Costs:** 10 bps fee + 5 bps slippage **per side**  
**Sizing:** 3% risk per trade, 95% max position, no account-level drawdown stop  
**Universe:** current Binance liquid majors/shortlist; one strategy per coin

This is a **two-year stability screen**, not a clean untouched out-of-sample
test. Both years were inspected when choosing the five. The honest use of this
note is to decide what deserves forward paper, not to claim a proven edge.

## Verdict

Five sleeves survived: a 4h breakout, a 4h capitulation-reversal, and three
short-hold 1d breakouts. Four are trend entries; XRP is the diversifying
mean-reversion sleeve.

| # | Coin | TF | Family | 2y return | Max DD | Trades | Win | PF | Avg R | Year 1 | Year 2 | Neighbour robustness | Bootstrap PF 95% |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | XRP/USDT | 4h | WVF + stochastic reversal | **+48.9%** | **-7.3%** | 19 | 63.2% | **2.98** | **+0.86R** | +7.3% | +38.8% | 47/84 | **1.21–9.30** |
| 2 | SUI/USDT | 4h | 240h breakout | **+57.8%** | -13.1% | 31 | 41.9% | 2.27 | +0.62R | +24.9% | +26.3% | **71/72** | 0.67–5.56 |
| 3 | INJ/USDT | 1d | 168h breakout, 3-day hold | +23.5% | **-5.3%** | 27 | 66.7% | 2.38 | +0.29R | +6.2% | +16.2% | 18/72 | 0.88–7.73 |
| 4 | TAO/USDT | 1d | 120h breakout, 3-day hold | +21.6% | -5.6% | 32 | 59.4% | 1.93 | +0.23R | +5.8% | +14.9% | 8/72 | 0.81–4.98 |
| 5 | AVAX/USDT | 1d | 288h breakout, 3-day hold | +13.3% | -6.1% | 20 | 50.0% | 1.79 | +0.22R | +5.6% | +7.3% | 16/18 | 0.49–5.58 |

"Neighbour robustness" is the number of local parameter variants that stayed
positive in **both** years, out of the variants tested around that sleeve.
"Bootstrap PF" is a 5,000-resample 95% interval of the full-period profit
factor. Only XRP clears 1.0 at the lower bound; SUI's case rests on its very
wide parameter plateau, not on a tight bootstrap.

"Avg R" is average dollar P&L divided by $30 (3% of a $1,000 sleeve), so it is
a **nominal R**; compounding makes actual fixed-fraction R slightly different.

## The five, exactly

### 1. XRP/USDT — 4h William Vix Fix + stochastic reversal

**Rules**

- WVF prints its **first green bar** of a panic cluster
  (`wvf >= upper_band` or `wvf >= range_high`).
- Stochastic %K crosses above %D while oversold (<20 at some point in the last
  5 bars).
- Close is **below** the 200-day own-coin moving average (buy panic inside a downtrend).
- Stop: 2.0 × ATR(14) below the signal close.
- Target: 2.0R.
- Time exit: 240 4h bars (40 days).
- No trail.

**Why it made the list**

- 19 trades, 63.2% wins, 2.98 dollar-weighted PF, +0.86R average.
- Max DD only 7.3%; year 1 +7.3%, year 2 +38.8% — both positive.
- 47 of 84 local variants stayed positive in both years.
- Best bootstrap interval of the five: PF 1.21–9.30, expectancy lower bound
  +0.15R.
- It is the only non-trend sleeve here, and its daily correlation with the
  other four is essentially zero (0.00–0.03).

**Weakness**

- Train year has only 6 trades; the edge is a panic-bounce pattern that can go
  long stretches without a signal.
- Fixed 2R target caps the right tail; most of the return came in year 2.

### 2. SUI/USDT — 4h 240h breakout

**Rules**

- Close > highest high of the prior 240 hours (60 bars), shifted by one bar.
- Close > 20-day EMA (480h).
- Stop: 2.0 × ATR(14) below the signal close.
- Trail: 2.5 × ATR(14) under the highest high since entry, ratcheted only up.
- Time exit: 72 hours (18 bars).

**Why it made the list**

- 31 trades, +57.8% over two years, 2.27 PF, +0.62R.
- Year 1 +24.9%, year 2 +26.3% — the most consistent return stream.
- **71 of 72 local variants positive in both years**, median worst-year
  +19.1%. This is the strongest parameter plateau in the study.
- At 30/15 bps costs it still makes +39.8%; at 50/25 bps +23.8%.

**Weakness**

- Win rate only 41.9%; it lives on a few large winners. The top 3 trades are
  114% of total P&L, so removing them leaves a much smaller edge.
- Bootstrap PF lower bound is 0.67; trade-level significance is weak.
- Exposure is only 4.5% of bars — it is a rare-signal breakout, not a constant
  participant.

### 3. INJ/USDT — 1d 168h breakout, 3-day hold

**Rules**

- Close > prior 168h (7 daily bars) high.
- Close > 20-day EMA.
- Stop: 1.75 × ATR(14) below the signal close.
- Trail: 3.0 × ATR under the highest high since entry.
- Time exit: 72 hours (3 daily bars).

**Why it made the list**

- 27 trades, +23.5%, 2.38 PF, 66.7% wins, +0.29R.
- Max DD 5.3%; year 1 +6.2%, year 2 +16.2%.
- 18 of 72 local variants positive in both years; the working region is
  around 120–168h lookback with a 3-day hold.

**Weakness**

- It is a **3-day burst**, not a multi-week swing. If the hold is extended to
  10–20 days the edge disappears in the grid.
- 72 variants tested, 18 survived: the plateau is real but not huge.
- Bootstrap PF lower bound 0.88.

### 4. TAO/USDT — 1d 120h breakout, 3-day hold

**Rules**

- Close > prior 120h (5 daily bars) high.
- Close > 20-day EMA.
- Stop: 2.0 × ATR(14).
- Trail: 3.0 × ATR.
- Time exit: 72 hours (3 daily bars).

**Why it made the list**

- 32 trades, +21.6%, 1.93 PF, 59.4% wins, +0.23R.
- Max DD 5.6%; both years positive (+5.8%, +14.9%).
- More trades than INJ and a slightly lower train drawdown.

**Weakness**

- Only **8 of 72** local variants survived, and every survivor uses the 3-day
  hold. This is the narrowest parameter region of the five.
- Top 3 trades are 81% of P&L.
- Treat it as the smallest of the trend allocations, not a core sleeve.

### 5. AVAX/USDT — 1d 288h breakout, 3-day hold

**Rules**

- Close > prior 288h (12 daily bars) high.
- Close > 20-day EMA.
- Stop: 2.0 × ATR(14).
- Trail: 3.0 × ATR.
- Time exit: 72 hours (3 daily bars).

**Why it made the list**

- 20 trades, +13.3%, 1.79 PF, 50% wins, 6.1% max DD.
- 16 of 18 local variants positive in both years — a wide, if low-return,
  plateau.
- Slowest and least correlated trend sleeve; useful as a small diversifier.

**Weakness**

- **One trade (2026-09-19, +37.5%) is 92% of the P&L.** Remove it and the
  sleeve is roughly +1%. That is a warning, not a diversified edge.
- Cap it at 10% of the sleeve book, or skip it if you want only four.

## Portfolio

Equal capital across the five, $1,000 each:

| Book | 2y return | Max DD | CAGR | Sharpe | Positive months | Worst month |
|---|---:|---:|---:|---:|---:|---:|
| Equal weight (20% each) | +33.0% | -3.13% | +15.2% | 1.94 | 58% | -1.44% |
| Confidence weight (XRP 30, SUI 25, INJ 20, TAO 15, AVAX 10) | **+38.4%** | **-2.98%** | **+17.4%** | **2.00** | 58% | -1.82% |

The confidence weights are an **ex-ante judgement**: XRP gets the most because
its bootstrap is the only one clearing 1.0; SUI gets the second-largest because
its parameter plateau is the broadest; AVAX gets the least because one trade
dominates it. They are not optimised weights.

On a **$1,000 account**, that is approximately:

- XRP $300
- SUI $250
- INJ $200
- TAO $150
- AVAX $100

At 3% risk per sleeve of its own slice, total heat if all five fire is about
3% of the account ($30), not 15%. The 95% position cap can still put most of
the account to work in notional terms.

## Robustness and cost stress

Full-period return at higher assumed costs (fee/slippage per side):

| Sleeve | 10/5 | 20/10 | 30/15 | 50/25 |
|---|---:|---:|---:|---:|
| XRP | +48.9% | +42.6% | +36.6% | +25.2% |
| SUI | +57.8% | +48.6% | +39.8% | +23.8% |
| INJ | +23.5% | +21.0% | +18.5% | +13.7% |
| TAO | +21.6% | +19.2% | +16.9% | +12.3% |
| AVAX | +13.3% | +11.5% | +9.7% | +6.1% |

All five survive 5× the base cost assumption. That is a cost-robustness check,
not a guarantee: alt-coin market orders at the next open can slip more than
5 bps in a fast tape.

## Why time exit, and not only TP/trail?

They are not alternatives. XRP has a fixed 2R target; SUI and the three daily
breakouts set a stop and a trailing stop. The time exit is a **maximum hold**,
and the ablation shows it is doing very different amounts of work in each
sleeve.

| Sleeve | Current exit | Same, no time exit | Fixed 2R + time | Fixed 2R, no time | Stop + time only |
|---|---:|---:|---:|---:|---:|
| SUI 4h | **+57.8%** (trail 2.5) | +55.3% | +28.3% | +7.2% | +35.9% |
| INJ 1d | **+23.5%** (trail 3.0) | **-6.7%** | +20.1% | -0.8% | +21.1% |
| TAO 1d | **+21.6%** (trail 3.0) | **-7.7%** | +18.5% | -6.4% | +21.6% |
| AVAX 1d | **+13.3%** (trail 3.0) | **-13.9%** | +7.5% | -18.3% | +13.3% |

Read this carefully:

- **SUI is a trailing-profit strategy.** The trail is the main exit; removing
  the time exit costs only 2.5 points. A fixed 2R target halves the return.
- **The daily sleeves are 3-day time-exit strategies.** The 3 ATR trail almost
  never fires inside 72 hours. If the time exit is removed, the trail gives the
  profit back and the sleeves lose money. "Stop + time only" is essentially
  identical to the current result for TAO and AVAX, and within 2.3 points for
  INJ.
- **XRP is a fixed-target strategy.** 2R is the best target tested:
  1.5R +36.2%, 2R **+48.9%**, 2.5R +46.5%, 3R +14.1%. The 240-bar (40-day)
  time exit never binds — the longest actual hold is about 368 hours (15
  days), still well inside the 40-day cap — so it is only a safety backstop.
  There is no trail in this sleeve.

A tighter trail does not replace the time exit. Without a time exit, the best
trail for INJ is 2.0 ATR (+12.3%), for TAO 2.0 ATR (+8.2%), for AVAX 1.0 ATR
(+5.4%), and for SUI 2.5 ATR (+55.3%) — all below the current time-exit
results except SUI, which is close.

The economic reason: a 1d breakout is a **3-day momentum burst**. After that
the signal has decayed and the trade starts giving back. The 3 ATR trail is
wider than the normal 3-day range, so it cannot exit soon enough to protect
the burst; the clock does. For SUI, the 4h breakout has a genuine right tail
and the trail can follow it. For XRP, the panic bounce has a defined
mean-reversion target, so 2R is the natural exit.

Practical consequence: **TAO and AVAX are identical in this sample to
stop + 3-day time**; INJ gives up 2.3 points if the trail is removed; only SUI
genuinely needs the trail. So the three daily sleeves can be made
live-compatible by dropping the inert trail, while SUI stays forward-only
until trail support exists.

## What I rejected, and why

The first-pass year-1 leaders all failed the next year:

| Year-1 leader | Year 1 | Year 2 |
|---|---:|---:|
| XRP capdip 240h d25% | +27.6% | -4.3% |
| ETH capdip 240h d20% | +37.1% | -14.9% |
| BNB capdip 240h d15% | +41.0% | -18.3% |
| SOL breakout 120h s2 t3 | +36.3% | -20.4% |
| ETH breakout 240h s2 t3 | +37.6% | -17.7% |

That is the per-symbol fitting trap in `research/PER_SYMBOL.md`: 4h one coin
gives ~9–16 decisions a year, so the best year-1 fit is usually noise. This is
why the shortlist uses a **two-year stability screen**, and why I am not
calling the result out-of-sample.

CAKE and SOL volume-climax looked strong in the first scan pass but failed the
exact parameter-neighbour rerun; they are not in the five. HYPE was skipped
because only 391 4h / 66 1d bars were available.

## Paper deployment (2026-11-28)

The five sleeves are shipped and active on the paper forward runner, one
$1,000 wallet each, at the measured profile (3% risk, 95% max position,
drawdown switch off):

| Sleeve | Symbol | TF | Params | Forward history | Trades | Max DD |
|---|---|---|---|---:|---:|---:|
| `swing_breakout_sui` | SUI/USDT | 4h | 60-bar high, 2.5 ATR trail, 18-bar cap | **+61.3%** | 30 | -13.1% |
| `wvf_stoch_xrp` | XRP/USDT | 4h | 2R target, 2 ATR stop, 240-bar cap | **+49.3%** | 18 | -7.3% |
| `swing_breakout_inj` | INJ/USDT | 1d | 7-bar high, 1.75 ATR stop, 3 ATR trail, 3-bar cap | **+23.5%** | 27 | -5.3% |
| `swing_breakout_tao` | TAO/USDT | 1d | 5-bar high, 2 ATR stop, 3 ATR trail, 3-bar cap | **+21.6%** | 32 | -5.6% |
| `swing_breakout_avax` | AVAX/USDT | 1d | 12-bar high, 2 ATR stop, 3 ATR trail, 3-bar cap | **+13.3%** | 20 | -6.1% |

These are **forward replays, not live demo orders**. `bot live` still has no
trail support, so SUI cannot be run there faithfully; the other four are
live-compatible once their explicit trades are scheduled. The forward
runner re-reads the manifests each cycle, and `live since deploy` will start
at $1,000 on the first cycle; the table above is the full history replay.

The strict two-year evidence for SUI is **+61.3% / -13.1% / 30 trades**
(2024-11-28 .. 2026-11-28). The +57.8% figure elsewhere in this report comes
from the 740-day alignment load used during research and is conservative;
both are the same rule and the difference is one boundary trade. XRP's
forward history reads 18 trades because the still-forming last 4h bar is
dropped; the evidence replay has 19 closed trades.

## Deployment notes — read before promoting

- The five manifests are active; no live-bot unit or order path was added.

- The research harness `SwingLab` is still in `research/cake/cake_swing.py`;
  the deployable port is `src/bot/strategy/swing_sleeve.py` plus the four
  per-coin breakout modules. The static sleeves carry the tuned defaults —
  there is no runtime parameter shelf.
- **The live loop does not implement `trail_atr`.** The forward replay
  (`bot forward`) does, because it uses `Engine`; `bot live` only honours stop,
  fixed target and strategy CLOSE signals. SUI therefore cannot be run
  faithfully as a live demo bot yet — forward shadow only, or add trail support
  first. TAO and AVAX can drop the inert trail and run as stop + 3-day time;
  INJ can do the same for 2.3 points less.
- XRP WVF is live-compatible: fixed 2R target, stop, and the strategy's own
  time-exit CLOSE.
- It is now running at 3% risk, 95% cap and the account drawdown switch off.
  Poll the 4h sleeves at 00/04/08/12/16/20 UTC and the 1d sleeves just after
  00:00 UTC.
- Add a **portfolio-level -15% kill switch** as an overlay. The measured max
  portfolio DD was only -3.0%, so -15% is ~5× the sample; if it hits, the
  regime is outside the sample and the book should stop.
- One bot per symbol; the existing live sleeves are UNI, JUP, RENDER, ARB and
  NEAR, so none of these five collide.

## Files

- `research/top5_promote.py` — reruns every shipped sleeve through `Engine`
  and writes the strict two-year evidence files in `research/top5/evidence/`.
- `research/top5_final.json` — full metrics, bootstrap intervals, equity and
  trade CSVs for each sleeve.
- `research/final_<SYMBOL>_<TF>_trades.csv` / `_equity.csv` — audit trail.
- `research/_top5_scan.py` — 18-coin, 42-config, 4h/1d scan.
- `research/_top5_neighbors.py` — local neighbourhoods for SUI/XRP/TAO.
- `research/_top5_alt_neighbors.py` — INJ/LINK/AVAX/ETH/BNB neighbourhoods.
- `research/_top5_final.py` — trade-level audit of the five.
- `research/_top5_costs.py` — cost stress.
- `research/_top5_portfolio.py` — correlation and combined equity.
