# Five sleeves, tuned on their own coin

One year, 4h bars, Binance spot, long only. The all-coin sweep
(`ALLCOINS.md`) picked five coin/rule pairs; this takes each pair and tunes
its own parameters on its own coin, then reports the standard analysis table
plus the probability checks that the headline number needs.

- **Window:** 2025-11-28 -> 2026-11-28 (365 measured days, 455 loaded so the
  warm-up burns outside the window).
- **Protocol:** signal on the close of bar *i*, fill at the open of bar *i+1*,
  10 bps fee + 5 bps slippage a side, 3% of equity at risk, $1,000 a wallet,
  stops checked before targets, both-hit bars scored as a loss.
- **Search:** 952 cells - NEAR/PYTH pullback 144 each, RENDER/ARB breakout 224
  each, JUP capitulation-dip 216. 844 cells clear the 12-trade floor.
- **Pick:** best full-year Calmar (= return / |max drawdown|) among cells with
  12+ trades and a drawdown no worse than -35%.


## Shipped: four of the five (2026-11-28)

PYTH is **not** shipped. Its pick is the weakest of the five (+42.6% but
-26.9% drawdown, Sharpe 1.09) and it sits on a spike: its neighbour median is
+10.9%, against +31.0/+32.7/+37.8% for NEAR/RENDER/ARB. The owner cut it from
the book, so there is no `swing_pullback_pyth` sleeve.

The other four ship as per-symbol sleeves in `src/bot/strategy/` (static
modules, tuned defaults baked in, one test each), promoted to paper as $1,000
wallets with the evidence below:

| coin | sleeve | year | trades | win | PF | Sharpe | promoted |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| NEAR | `swing_pullback_near` | +86.0% / -12.9% | 61 | 49.2% | 1.73 | 2.23 | `promotions/paper/swing_pullback_near.json` |
| RENDER | `swing_breakout_render` | +70.1% / -11.9% | 34 | 55.9% | 2.24 | 2.31 | `promotions/paper/swing_breakout_render.json` |
| ARB | `swing_breakout_arb` | +67.2% / -8.4% | 30 | 56.7% | 2.33 | 2.60 | `promotions/paper/swing_breakout_arb.json` |
| JUP | `capitulation_dip_jup` | +58.8% / -7.9% | 32 | 62.5% | 2.67 | 2.62 | `promotions/paper/capitulation_dip_jup.json` |

Live as of 2026-11-28: `tradingbot@near`, `tradingbot@render`, `tradingbot@arb`
(4h, $1,000 each, 3% risk) plus the forward replay of all four. JUP has no live
slot: `jup_liquidity_15m` already trades that symbol, and two bots on one
symbol share one demo balance and would close each other's position. Its 4h
sleeve therefore accrues its record in the forward replay only.

`research/five_sleeves_promote.py --write` regenerates the four evidence files
in `research/evidence/`; each one replays the *shipped default* through
`Engine` and must reproduce the table above before a manifest is stamped. The
honesty caveats further down are unchanged: picks are best-of-hundreds on the
reported year, so the forward record - not this table - is the test.

### Cross-check: the window one year earlier

The forward runner replays 730 days (2-year cap) at the same 3%/95% profile,
so it also covers the year *before* the tuning window.  Split the two-year
equity by year and the tuning year stops looking like the rule:

| sleeve | tuned year | year before it | 2y return / dd | 2y trades |
| --- | ---: | ---: | ---: | ---: |
| `swing_pullback_near` | +86.0% | **-32.5%** | +25.6% / -36.3% | 111 |
| `swing_breakout_render` | +70.1% | +6.9% | +81.8% / -12.3% | 63 |
| `swing_breakout_arb` | +67.2% | +5.7% | +76.7% / -26.2% | 61 |
| `capitulation_dip_jup` | +58.8% | **-26.0%** | +17.5% / -33.8% | 98 |

(Derived from the runner: year-before = (1 + 2y) / (1 + tuned year) - 1, so it
is approximate - the earlier year's equity changes every later position size.
The 2y drawdown is deeper than either year alone for the same reason.)

RENDER and ARB earn their keep in both years.  NEAR and JUP do not: their
headline comes from one regime, and over two years JUP is +17.5% with a
-33.8% drawdown and NEAR +25.6% with -36.3%.  Neither prior year was traded by
the tuner, so this is the closest thing to an out-of-sample check these four
have - and it says two of the four are regime bets, not edges.  They stay
promoted (paper money, forward record decides), but the forward line is the
unflattering one and the one to quote.

## The five

| coin | rule | trades | win (95% CI) | PF | exp R | net | max DD | Sharpe | Sortino | Calmar | in mkt | H1 | H2 | max loss streak |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NEAR | pullback f24/t480 s2 rr2 h120 | 61 | 49.2% (37-61) | 1.73 | 0.36 | **+86.0%** | -12.9% | 2.23 | 4.40 | 6.69 | 19.2% | +50.2% | +23.9% | 5 |
| RENDER | breakout 120h t480 s1.5 rr2 h72 | 34 | 55.9% (39-71) | 2.24 | 0.54 | **+70.1%** | -11.9% | 2.31 | 5.54 | 5.90 | 7.5% | +47.3% | +15.5% | 3 |
| ARB | breakout 168h t480 s1.5 rr2 h72 | 30 | 56.7% (39-73) | 2.33 | 0.59 | **+67.2%** | -8.4% | 2.60 | 6.62 | 7.99 | 5.1% | +10.1% | +51.8% | 2 |
| PYTH | pullback f12/t480 s1.5 rr3 h72 | 79 | 38.0% (28-49) | 1.28 | 0.18 | **+42.6%** | -26.9% | 1.09 | 2.04 | 1.59 | 23.1% | +7.5% | +32.7% | 6 |
| JUP | capdip 240h d0.20 s2 rr1.5 h120 | 32 | 62.5% (45-77) | 2.67 | 0.50 | **+58.8%** | -7.9% | 2.62 | 5.14 | 7.49 | 9.7% | +17.6% | +35.0% | 2 |

Every pick beat the config the sweep had chosen: NEAR +68.3 -> +86.0 (dd
-14.9 -> -12.9), RENDER +56.1 -> +70.1, ARB +45.6 -> +67.2 (dd -12.9 -> -8.4),
PYTH +32.0 -> +42.6, JUP +28.1 -> +58.8 (dd -9.0 -> -7.9). Some of that is
real (the sweep only tested 26 canned configs) and some is the extra fitting.

## Probability, not just the headline

`P(year > 0)` is a 5,000-draw bootstrap of the same trades in random order at
3% risk; the percentiles are the spread of a year built from that trade
distribution. `neighbours` is the median return of the cells one grid step
away - the cheapest test of whether the pick is a plateau or a spike.

| coin | P(>0) | boot median | 5th pct | median DD | 5th pct DD | neighbours | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| NEAR | 97.2% | +83.4% | +9.3% | -16.1% | -29.1% | +31.0% | plateau |
| RENDER | 98.4% | +67.2% | +13.5% | -10.5% | -19.7% | +32.7% | plateau |
| ARB | 97.5% | +64.1% | +9.2% | -10.5% | -20.4% | +37.8% | plateau |
| PYTH | 78.0% | +37.1% | -28.3% | -27.0% | -46.4% | +10.9% | thin |
| JUP | 99.1% | +59.5% | +16.0% | -8.2% | -15.6% | +8.7% | spike |

## The book: five $1,000 wallets

| | value |
| --- | ---: |
| equity | $5,000 -> $8,247 |
| return | +64.9% |
| max drawdown | -7.3% |
| Sharpe / Sortino / Calmar | 3.25 / 7.38 / 8.84 |
| halves | H1 +26.5%, H2 +30.4% |
| months | 9 of 12 positive, best +21.1%, worst -0.8% |
| annualised vol | 16% |

Daily-return correlation between the sleeves is 0.18-0.32, and JUP is
uncorrelated with the other four (-0.02 to +0.03). That is why the book's
drawdown (7.3%) is a quarter of the worst sleeve's (26.9%): the sleeves are
long different coins at different times.

## What this does and does not say

- **The pick is fitted on the year it reports.** Calmar-ranked over 144-224
  cells per coin, so the headline is the top of a distribution, not its mean.
  The bootstrap and the neighbour median exist precisely to price that in.
- **Thirty to eighty trades is enough to rank a rule, not to trust a Sharpe**
  above 2. ARB's 2.60 rests on 30 trades; one trade is 3.3% of the sample.
  The Wilson intervals are the honest width of the win rate.
- **H1/H2 are not a holdout.** Splitting one year in half after selecting on
  the whole year does not create out-of-sample data; it only shows whether the
  P&L was spread out. ARB made +10.1% then +51.8%, NEAR the reverse - both
  orderings are invisible in advance.
- **PYTH and JUP are the weak claims.** PYTH needs a 3R target to pay, wins
  38% of the time, spends 23% of the year in the market (most of any sleeve) and
  its neighbours median +10.9%. JUP's shape is the cleanest of the five but its
  neighbours median +8.7%: it sits on a spike in stop_atr, and the sweep's
  original s2.5 version made +28.1% - the range is +9% to +59%, not +59%.
- **Costs.** 15 bps round trip fits these pairs at $1,000. At $10,000 PYTH,
  JUP and RENDER will slip more than modelled.
- **One regime.** 2025-11 -> 2026-11 includes a broad drawdown (BTC -33.8%,
  equal-weight basket -39.8% on this window), so a long-only book that ends
+65% is a statement about these five coins' trends, not about the next year.

## Next step

Run the five forward on paper and compare trade-by-trade against this table.
Tuning them again on the same 365 days only moves the fit.

Files: `five_sleeves.py` (grid + stats), `five_sleeves.csv` (all cells),
`five_sleeves_picks.csv`, `five_sleeves_book.py` + `five_sleeves_book.json`,
`five_sleeves_report.py` -> `five_sleeves.html`.
