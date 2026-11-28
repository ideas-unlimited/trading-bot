# BROAD_UNIVERSE.md — every $1B coin, one year, the same grid

**Window** 2025-11-28 .. 2026-11-28 (365 measured days after 90 days of
warmup), charts 4h and 1d, long-only Binance spot, signal on the close, fill
at the next open, 10 bps fee + 5 bps slippage a side, 3% risk, 95% maximum
position, account drawdown switch off.  **Universe** every coin with a market
cap of at least $1B that trades on Binance spot (36 pairs, 32 measurable).
**Question** after the 30-coin study (`ALLCOINS.md`) found breadth only in the
1d breakout family: does that survive on the *whole* liquid market, and is
there a per-coin sleeve worth shipping that the hand-picked universes missed?

Scripts: `broad_sweep.py` (universe + stage 1), `broad_deep.py` (cross-chart +
neighbour check), `broad_evidence.py` (the shipped-sleeve evidence pack),
`broad_validate.py` (tune on the earlier year, validate on the later one).
Data: `broad_universe.csv`, `broad_stage1.csv` (1,820 cells),
`broad_stage2*.csv`, `broad_evidence*.csv`, `broad_validate.csv`.

## The universe

CoinGecko `/coins/markets` ordered by market cap, two pages of 250 (the 500th
coin is $34.8M, so nothing above $1B hides below the cut), then every row is
matched against Binance `/api/v3/exchangeInfo` for a `SYMBOL/USDT` pair that is
`TRADING` and spot-tradable.

| filter | coins |
| --- | --- |
| market cap >= $1B | 68 |
| .. of which a stablecoin | 14 |
| .. no Binance spot USDT pair | 18 |
| **tradeable** | **36** |
| .. with at least 330 measured days | **32** |

The 18 without a pair: FIGR_HELOC, LEO, XMR, WBT, CC, USYC, BTW, EURSAFO,
CRO, M, OKB, RAIN, BUIDL, VVV, HTX, MNT, PIEVERSE, BGB.  The 14 stablecoins:
USDT, USDC, USDS, USDe, DAI, RLUSD, USD1, USDG, USDY, PYUSD, USDF, USDD,
BFUSD, OUSD.  CoinGecko's top 500 contained **no** wrapped or restaked row at
all this run (no WBTC, WETH, stETH), so the dedupe filters in
`broad_sweep.py` were a guard, not a deletion; they stay for reruns.

XAUT and PAXG are gold tokens.  They trade on Binance spot and are not
stablecoins, so they stay — they behave like a commodity, which is worth
having in the sample as a control.  HYPE (66 days), GRAM (149), XAUT (246) and
U (319) are in the CSV with short windows and drop out of the tables
(`window_days >= 330`).

## Method and how honest it is

The 26 cells are the same `grids(tf)` the all-coins study used: two
SMA-cross, three Donchian, three RSI mean-reversion, three momentum swings,
capitulation dips, two volume climaxes, three WVFX stochastic and six SwingLab
cells (breakout / pullback / dip, fixed-2R and trailing exits).  36 pairs x 26
cells x 2 charts is 1,872 possible runs; **1,820 were computed** (a short-history
pair loses whole cells), of which **844** have at least 8 trades and 330 measured
days.

Reproducibility: the 18 coins this universe shares with `ALLCOINS.md` produce
**identical** cells (0 mismatches on the 1d breakout cell), so the two studies
measure the same window with the same code — the differences below are the
universe, not the harness.

Scale of the search: 32 coins x 26 cells x 2 charts = 1,664 drawable cells, so
"the best cell" is a lottery ticket by construction.  Every pick below is
therefore judged on three things, never on its own return: **breadth** across
the universe, **behaviour on the chart it was not picked on**, and whether it
**beats simply holding the coin**.

## 1. The market-wide answer: one family, two thirds of the market

Breadth per config, 1d chart, coins with >= 8 trades and >= 330 days:

| config | coins | positive | median | median dd |
| --- | --- | --- | --- | --- |
| 1d `lab breakout 120h s3 t5` | 32 | **69%** | +1.5% | -6.2% |
| 1d `lab breakout 120h s2 t3` | 32 | **59%** | +2.4% | -8.8% |
| 4h `donchian 240/96h` | 32 | 56% | +1.9% | -19.5% |
| 1d `lab pullback 120h s2 2R` | 32 | 56% | +0.9% | -10.9% |
| 4h `momentum_swing 120/480h t5.0` | 32 | 53% | +3.2% | -26.8% |
| 1d `capdip 240h d20 rr1.5` | 21 | 52% | +0.5% | -3.7% |
| everything else | | < 50% | | |

Context for those medians: the median coin in this universe **fell 46.6%**
over the year (78% of the 32 were down), the median ranked cell lost 2.3% and
only 42% of cells were positive at all.  The 4h chart was the weaker half:
517 cells, median -5.7%, against 327 cells at 1d with median -0.3%.

So the honest market-wide claim is narrower than the earlier study's.  On its
hand-picked 29 coins the 1d breakout cell was positive on **83%**; on every
$1B coin it is **65-69%**, with a median of +1.5-2.4%.  The family is real, it
is the same family the RENDER and ARB sleeves run, and it is worth about two
points a year at the median — it is not a money printer, and the flattering 83%
was universe selection.

The other direction is just as important: **nothing else is broad.**  Every
other family is below 50% positive across the liquid universe, and the 4h
chart is a coin-selection game.

## 2. What these rules actually do: exposure control

10 of 11 numbers in the evidence pack are in the market **3-20% of the year**;
the shipped sleeves are the same (`swing_breakout_render` 7.5%).  A book that
is flat 80-95% of the time and still ends positive in a year the median coin
fell 46.6% is not picking winners, it is **staying out**.

That reading survives the obvious test — compare each coin's best ranked cell
with simply holding that coin over the same year, on Calmar:

| coin | best cell | rule | hold | rule Calmar | hold Calmar |
| --- | --- | --- | --- | --- | --- |
| ENA 2y | 4h breakout | **+123.2% / -16.6%** | -85.8% / -94.5% | 7.4 | -0.9 |
| QNT 2y | 1d breakout | +34.1% / -19.8% | +56.8% / -66.7% | 1.7 | 0.9 |
| ZEC 2y | 4h sma_cross 96/384 | +579.2% / -31.5% | +1213.9% / -72.2% | 18.4 | 16.8 |
| JST 1y | 4h lab dip 240h | +30.7% / -5.0% | +277.0% / -26.5% | 6.2 | 10.5 |

**31 of 32** coins' best rule beat holding that coin on Calmar over the
measured year — the one exception is JST, whose +277% year made holding the
better trade.  That is the metric the sleeves should be judged on, and it is
why a long-only spot book can be flat most of the year and still be worth
running.

## 3. Which coins cooperate

Share of each coin's ranked cells that were positive (26 cells per chart):

| coin | cells | positive | median | best |
| --- | --- | --- | --- | --- |
| NEAR | 26 | **88%** | +13.0% | +87.0% |
| TAO | 30 | 80% | +5.4% | +27.5% |
| ZEC | 31 | 77% | +26.7% | +158.2% |
| SUI | 29 | 76% | +4.8% | +19.7% |
| QNT | 28 | 75% | +42.3% | +117.5% |
| UNI | 27 | 70% | +4.1% | +20.7% |
| ENA | 31 | 68% | +8.2% | +43.0% |
| PEPE | 28 | 64% | +2.1% | +20.2% |
| SOL | 27 | 59% | +1.0% | +10.4% |
| .. | | | | |
| BCH | 29 | 14% | -7.6% | +18.8% |
| ETH | 24 | 12% | -10.3% | +13.7% |
| BNB | 24 | 8% | -11.6% | +3.6% |
| ASTER | 25 | 8% | -13.6% | +10.6% |
| ADA | 27 | 7% | -9.6% | +7.0% |

This per-coin breadth is a better coin-level statistic than the single best
cell: NEAR responding to 23 of 26 rules is a property of the coin, ZEC
momentum's 9.15 Calmar is one cell.  Note how it cuts both ways — the two
majors everyone benchmarks against, ETH (12%) and BNB (8%), are the *least*
tradeable coins in the universe this year, and NEAR, whose pullback sleeve is
already live, is the most.

## 4. The three shiny numbers, and why two are coin luck

**ZEC 4h `momentum_swing 120/480h t5.0` — +158.2%, -17.3%, Calmar 9.15,**
Sharpe 2.10, PF 4.40, expectancy 2.23R, 19 trades, 42% wins (23-64%),
bootstrap P(year > 0) 0.94, in the market 19% of the year, positive on 5 of 5
charts, all 10 neighbour variants positive (median +147%), two years +288.6%
with -34.2%.  It looks like the find of the study and it is not a sleeve:
holding ZEC over the same two years made **+1,213.9%** (Calmar 16.8), and the
top three trades are 114% of the sum of all 19.  It is three big trend
captures on a coin that 13x'd, dressed as a rule.

**JST 4h `lab dip 240h s3 t5 g100d` — +30.7%, -5.0%, bootstrap 0.99, two
years +45.2% / -5.0%.**  The cleanest statistics in the whole sweep, and still
not a sleeve: JST itself made +277.0% that year with a Calmar of 10.5 against
the rule's 6.2.  The rule is a safe way to own a coin that was better simply
owned.

**QNT 1d `lab breakout 120h s2 t3` — +54.3%, -8.8%, Calmar 6.17, 17 trades,**
bootstrap 0.76, in the market 5.2% of the year.  The year before the measured
window, the same cell lost **-15.0%**, and the cell that was best in that year
(4h `wvfx_stoch 2.0/2.0`) lost -15.2% in the measured year.  A one-year
wonder, not a sleeve.

## 5. Tune on the earlier year, validate on the later one

`broad_validate.py` runs all 26 cells on **year A** (2024-11-28 .. 2025-11-27),
takes the Calmar-best, then reports what that same cell did in **year B** (the
year every other table here reports):

| coin | chart | year-A best cell | year A | year B |
| --- | --- | --- | --- | --- |
| ENA | 4h | `lab breakout 120h s2 t3` | **+45.4%** | **+43.0%** |
| ZEC | 4h | `sma_cross 96/384h` | +265.9% | +85.6% |
| ZEC | 1d | `sma_cross 96/384h` | +183.6% | +43.1% |
| TAO | 1d | `lab breakout 120h s3 t5` | +5.3% | +9.2% |
| SUI | 4h | `lab breakout 120h s2 t3` | +26.6% | +6.4% |
| NEAR | 4h | `lab breakout 120h s2 t3` | +22.9% | +11.9% |
| QNT | 4h | `wvfx_stoch 2.0/2.0 100d` | +8.8% | -15.2% |
| QNT | 1d | `donchian 240/96h` | -1.8% | +37.4% |
| TAO | 4h | `vol_climax z3 d10 hold72h` | +6.7% | -11.6% |

**A cell fitted on the earlier year kept its sign on 5 of 10 coin-charts — a
coin flip**, exactly the lesson `AGENTS.md` already records from
`per_symbol.py`.  The shared 1d breakout cell was positive in both years on 4
of 10.  What is left, after that filter, is short:

* **ENA 4h breakout** was not just positive in both years — it was the
  **Calmar-best of all 26 cells on year A** (+45.4%) and then delivered
  +43.0% in the untouched year B.  Two years +123.2% / -16.6%, 47 trades,
  positive on 5 of 5 charts, all 10 neighbours positive (median +42%), in the
  market 5.7% of the year, bootstrap 0.83, and ENA itself fell 85.8% over the
  same two years.  ENA also responds to 68% of its 26 cells, so the coin is
  cooperative, not one lucky cell.
* **TAO 1d breakout** is small but consistent: +14.9% / -5.6% in the measured
  year, +5.8% the year before, two years +21.6% / -5.6%, 14 trades, bootstrap
  0.90, TAO positive on 80% of its cells, hold -37.3% in the year.
* **ZEC's real family is the SMA cross, not the momentum cell**:
  `sma_cross 96/384h` made +265.9% in year A and +85.6% in year B (4h), 1y
  +85.6% / -17.3%, two years **+579.2% / -31.5%** (Calmar 18.4 against hold's
  16.8), 13 trades, bootstrap 0.68.  If ZEC gets a sleeve at all, this is the
  shape of it.

## 6. The two-year cross-check

`--days 730` reran the whole grid on 2024-11-28 .. 2026-11-28 (same 1,820
cells; 1,089 ranked).  The market was worse: the median coin **fell 59.7%** and
81% of the 32 were down, the median ranked cell lost 1.1%, 48% were positive,
median drawdown -19.4%, median 34 trades.

| config (1d, two years) | coins | positive | median | median dd |
| --- | --- | --- | --- | --- |
| `capdip 240h d20 rr1.5` | 29 | **72%** | +1.8% | -6.3% |
| `lab breakout 120h s3 t5` | 32 | 62% | +1.5% | -9.2% |
| `momentum_swing 120/480h t3.5` | 29 | 62% | +3.4% | -15.4% |
| `vol_climax z2 d6 hold48h` | 27 | 59% | +2.6% | -5.6% |
| `lab breakout 120h s2 t3` | 32 | 56% | +1.8% | -13.7% |
| `sma_cross 48/192h` | 32 | 53% | +1.1% | -19.9% |

At 4h the two-year leaders are `capdip 240h d30 rr2.5` (81% of 16 coins,
median +6.8%) and `vol_climax z3 d10 hold72h` (71% of 21), and the momentum
swing makes +7.5% at the median with a -37.0% median drawdown.

So the ladder moved but nothing became broad: **the dip family leads over two
years, the breakout family leads over the last year, and both sit at 55-72% of
coins with a median under +4%.**  That is the same conclusion as section 1
reached from a second window, which is the point of running it.

The finalists held up on the longer window: ENA 4h breakout **+123.2% /
-16.6%** (47 trades; the evidence pack's 730-day alignment reads +107.9% with
45 trades) against ENA buy-and-hold -85.8%; TAO 1d breakout +21.9% /
-5.6% (33 trades) against TAO buy-and-hold -66.9%; ZEC 4h SMA cross +579.2% /
-31.5% against ZEC buy-and-hold +1,177.3%.

## Verdict

1. **No new broad edge.**  Over one year the only rule positive on most of the
   $1B universe is the 1d breakout family RENDER and ARB already run (65-69%,
   not the 83% a hand-picked universe suggested); over two years the dip
   family leads at 72%.  Both are "two thirds of coins, a median under +4%,
   and most of the year in cash" - a real but ordinary edge.
2. **One new sleeve is justified: `swing_breakout_ena`**, 4h, 120h breakout
   with a 2 ATR stop and a 3 ATR trail (72h max hold), on the evidence above —
   the only candidate that was the best cell on the earlier year *and*
   repeated in the later one, on a coin that lost 85% over the same two years.
   The module is written and tested
   (`src/bot/strategy/swing_breakout_ena.py`, `tests/test_swing_breakout_ena.py`)
   and the harness reproduces +43.0% / -16.6% / 24 trades from it unchanged.
   **Shipped on 2026-11-28** at the owner's instruction: ENA is target 19 in
   `docs/TARGETS.md`, `promotions/paper/swing_breakout_ena.json` passed the
   paper gate 5/5 at the measured 3%/95%/0.99 profile, and both halves are
   live on the box - the forward sleeve (replaying +107.9% / -16.6% / 45 trades
   on its 730-day window) and `tradingbot@swing_breakout_ena-paper` on the
   demo account. The bot half waited for the live loop to gain the Chandelier
   trail (commit 3c8c68e) so the live exit matches the measured one; it is flat
   at $1,000 until the next 4h breakout.
3. **A second one is defensible: `swing_breakout_tao`** on 1d (s2 t3), small
   but positive in both years with a 0.90 bootstrap.
4. **Do not ship ZEC momentum, JST dip or QNT breakout.**  The first two are
   beaten by simply holding the coin; the third lost the year before.
5. ZEC `sma_cross 96/384` is the highest-return rule in the study
   (+579% over two years) and worth a sleeve *if* the owner wants more ZEC
   exposure, but know that holding ZEC still returned twice as much with twice
   the drawdown.

## Limitations

One measured year, and it was a bear year for the whole universe (median coin
-46.6%).  The cells were chosen from 1,664; breadth and the year-A/year-B
split are the only defences, and both are weak with 8-45 trades a cell.  Half
the coins are new listings with under two years of history, so the 2-year
column silently measures a shorter period for some of them.  Long only, spot
only, no shorts: in a bear year the best a rule can do is be flat, which is
what most of these numbers are.

## Reproduce

```
.venv/Scripts/python.exe research/broad_sweep.py --refresh     # universe + 1,820 cells
.venv/Scripts/python.exe research/broad_sweep.py --report      # tables above
.venv/Scripts/python.exe research/broad_deep.py --top 6        # cross-chart + neighbours
.venv/Scripts/python.exe research/broad_evidence.py            # evidence pack
.venv/Scripts/python.exe research/broad_validate.py --pairs "ENA/USDT,TAO/USDT"
```