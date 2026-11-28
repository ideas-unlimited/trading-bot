# All-coin strategy sweep - one year

30 Binance spot pairs x 26 configurations x 2 charts (4h, 1d) = 1,534 cells,
window **2025-11-28 to 2026-11-28** (365 days measured, 455 loaded for
warmup). Long only, next-bar-open fills, 10 bps fee + 5 bps slippage a side,
3% risk per trade capped at 95% of equity, `max_drawdown_stop=0.99` so a
halted window cannot truncate a result.

Question: **which rule, on which coin, made the most money over the last
year, and is it a rule or a coin?**

## Method

* Universe: the 18 tracked symbols in `config/` plus 14 cached extras (30
  total, 28-29 with enough history at both charts).
* Configurations: 3 `sma_cross`, 3 `donchian`, 3 `rsi_meanrev`, 3
  `momentum_swing`, 3 `capitulation_dip_uni`, 2 `vol_climax`, 3 `wvfx_stoch`,
  and 6 `SwingLab` families (breakout / pullback / range) - windows are in
  **hours and converted to bars per chart**, so "120h" means the same
  economics on 1h and on 1d. See `allcoins_sweep.py:grids()`.
* Stage 1 (`allcoins_sweep.py` -> `allcoins_stage1.csv`): every cell once.
* Stage 2 (`allcoins_deep.py` -> `allcoins_stage2*.csv`): the finalists
  re-run on 1h/4h/6h/12h/1d, plus each of the first six numeric parameters
  perturbed x0.8 and x1.25 as a neighbour check.
* Stage 3, the families the briefs added: `rotation.py` (cross-sectional
  momentum, 72 configs), `grid_ladder.py` (long-only grid, 920 runs),
  `gates.py` (BTC-trend and correlation entry gates on the eight best
  sleeves).

## The shape of the year

Most alts fell 20-80% (BTC -33.8%, equal-weight basket -39.8%), so the
baseline is brutal: 90.7% of cells beat buy-and-hold simply by not holding
through the declines. Of the 769 cells with 8+ trades, **41% ended positive**
and only **14.6% were positive in both halves**.

Family medians (8+ trades):

| family | 4h pos | 4h median | 1d pos | 1d median |
| --- | --- | --- | --- | --- |
| lab (breakout/pullback) | 36% | -7.8% | **69%** | **+2.6%** |
| momentum_swing | 48% | -0.8% | 0% (3 cells) | -8.5% |
| vol_climax | 54% | +0.3% | - | - |
| wvfx_stoch | 43% | -1.7% | - | - |
| donchian | 35% | -6.5% | 39% | -2.3% |
| sma_cross | 30% | -9.9% | 40% | -1.1% |
| capdip | 23% | -9.4% | 33% | -2.0% |

## Top five by profit (one year, 4h)

| coin | config | year | max dd | trades | H1 | H2 | coins + | charts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NEAR | sma_cross 96/384h | **+87.0%** | -29.6% | 10 | -4.1% | +95.0% | 45% | 5/5 |
| NEAR | momentum_swing 120/480h t5.0 | +74.2% | -19.5% | 19 | +12.6% | +54.8% | 45% | 5/5 |
| NEAR | lab pullback 120h s2 2R | +68.3% | -14.9% | 70 | +36.3% | +23.5% | 21% | 4/5 |
| RENDER | lab breakout 120h s2 t3 | +56.1% | -12.2% | 23 | +43.7% | +8.6% | 41% | 4/5 |
| ARB | lab breakout 120h s2 t3 | +45.6% | -12.9% | 21 | +1.9% | +42.8% | 41% | 5/5 |

"coins +" is the share of all 29-30 coins the same configuration was
profitable on. None of the five clears half; the NEAR pullback clears a
fifth. The 1d breakout family is the only thing that does (see below).

Neighbour check (median return of the 8 nearest parameter perturbations):
NEAR pullback +63.7%, RENDER breakout +55.0%, ARB breakout +47.0%, NEAR
momentum +69.8%. The cells sit on plateaus, not on spikes - which is why
they are worth writing down even though the breadth is weak.

### One sleeve per coin

| coin | config | year | max dd | trades | win | H1 | H2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NEAR | lab pullback 120h s2 2R | +68.3% | -14.9% | 70 | 47% | +36.3% | +23.5% |
| RENDER | lab breakout 120h s2 t3 | +56.1% | -12.2% | 23 | 43% | +43.7% | +8.6% |
| ARB | lab breakout 120h s2 t3 | +45.6% | -12.9% | 21 | 43% | +1.9% | +42.8% |
| PYTH | lab pullback 120h s2 2R | +32.0% | -25.6% | 66 | 44% | +5.1% | +25.6% |
| JUP | capdip 240h d20 rr1.5 | +28.1% | -9.0% | 35 | 63% | +14.8% | +11.6% |

JUP is the cleanest evidence in the whole sweep: 35 trades, 63% win rate,
positive on all five charts, and the neighbour median is identical to the
cell (+28.1%), so the exact parameters are not the source of the number.

## The breadth finding

At 4h nothing is broad. The best any 4h configuration reached was 56% of
coins positive (`vol_climax z2 d6`, median +0.4%; `momentum_swing
240/960h t3.5` 55%, median +0.5%) - an edge you cannot size.

At 1d the same lab family flips:

* `lab breakout 120h s3 t5`: **24 of 29 coins positive (83%)**, median
  +2.9%, mean +3.3%, average drawdown -5.7%, 16.6 trades a year.
* `lab breakout 120h s2 t3`: 20 of 29 (69%), median +3.8%.

Small per coin, and only 8 of 29 were positive in both halves, so the edge
is thin and leans on the second-half rally. But it is a **market property**
rather than a coin property, which is what the 4h winners are not.

At 1h turnover eats everything: the NEAR pullback makes -20.9% there (125
trades) and TRX momentum -20.9% (123). Only low-frequency 1h configs survive
(NEAR sma_cross +177.8% on 10 trades - same ten trades, more noise).

## The families the two briefs added

| family | test | result |
| --- | --- | --- |
| Momentum ranking (cross-sectional) | 28 coins, 20/40/60-day lookback, top 1/2/3/5, daily or weekly rebalance, optional 100-day filter - 72 configs | **4% positive.** Best: daily, 20-day, top-2, +15.1% with a -59.8% drawdown and 154x turnover. Basket -39.8%, BTC -33.8%. |
| Grid (long-only ladder) | 920 runs: buy 3-5% under a 30/90-day high, 4 or 6 levels, take profit 3-5% | **4% positive**, median -9.3%. 4h: median -9.6%; 1d: -9.1%. Inventory accumulates into a downtrend and never gets the bounce. |
| Regime gate (BTC 100-day) | the 8 best sleeves, entries blocked while BTC is below its own 100-day average | Average drawdown -13.2% -> -9.5%, Calmar better on 4 of 8, return usually cut too (NEAR +68.3 -> +65.4, RENDER +56.1 -> +17.0). A risk tool, not a return tool. |
| Lead-lag gate (BTC correlation) | same sleeves, entries blocked while 30-day correlation to BTC > 0.85 | Better on 2 of 8 returns. No usable signal. |
| Chop gate (efficiency ratio < 0.30) | same sleeves | **Untested**: the threshold blocked every entry on 7 of 8 sleeves, so it measured nothing. Needs a recalibrated threshold. |

Not testable on long-only spot with the stored data: funding-rate
arbitrage (needs perps/shorts), market making (needs maker rebates and
inventory), liquidation cascades (no open-interest/funding history), and
statistical arbitrage in its real form (needs a short leg).

## Verdict

1. The most profitable cells are 4h NEAR sleeves, +68% to +87%, and they are
   coin-specific. Three of the top five are the same coin.
2. Only one thing looked like a market edge: the 1d breakout family, 69-83%
   of coins positive, +3% median. Too small to stand alone, right base to
   build on.
3. Grid, rotation and correlation gating are closed. The BTC trend filter is
   worth keeping as a drawdown knob on any sleeve that ships.
4. Selection caveat: the five winners came out of 1,534 tests. The
   neighbour and cross-chart checks are there to catch a spike, and they
   pass, but one year of one coin is still one sample.

## Files

| file | what |
| --- | --- |
| `allcoins_sweep.py` | stage 1 grid, writes `allcoins_stage1.csv` |
| `allcoins_deep.py` | cross-chart + neighbour check, writes `allcoins_stage2*.csv` |
| `rotation.py` | cross-sectional momentum, `rotation.csv` |
| `grid_ladder.py` | long-only grid, `grid_ladder.csv` |
| `gates.py` | BTC-trend / correlation / chop entry gates, `gates.csv` |
| `allcoins_report.py` | renders `allcoins_report.html` from the CSVs |

Run from the repo root with `$env:PYTHONPATH="src"` and the venv python.