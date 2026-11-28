# CAKE swing: 15m vs 30m vs 1h

**Window** 2025-11-28 -> 2026-11-28 (the 12-month cap for sub-4h charts).
Buy & hold over it: **-36.5%**. Long only, spot, signal on the close, fill
at the next open, 10 bps fee + 5 bps slippage per side, 3% risk per trade,
$1,000 start.

**Question** which of the three charts carries a swing entry worth running on
CAKE, with all entry families on the table.

**Script** `cake_swing.py` (stage 1 = the sweep, `--stage 2` = the gate sweep),
`cake_report.py` renders `cake_report.html`. Outputs `cake_swing_tf.csv`,
`cake_gate.csv`.

## Protocol

One causal run per config, split 60/40 at **2026-07-05**. Selection score is
the worse of the two IS halves; OOS is never used to pick. Every window is
given in **hours** and converted to bars inside `prepare()` (a 2-day breakout
is 192 bars at 15m, 96 at 30m, 48 at 1h), because a bar-denominated window
silently rescales with the chart. The 10% account drawdown kill switch is off
(`max_drawdown_stop=0.99`): left on, it fires mid-window, flattens the rest of
the test and scores a halted config on fewer decisions than a running one.

Grid, 53 configs per chart: three families (breakout over N-hour high, pullback
to the 24h EMA reclaimed, RSI(14) reclaim of 30) x lookback 48h/120h x stop 2/3
ATR x exit (2R target, 3R target, 5 ATR trail, 3 ATR trail), 72h max hold,
20-day EMA trend gate - plus `donchian`, `momentum_swing` and `rsi_meanrev`
with the same windows expressed in hours.

## Sweep result: nothing survives the selection rule

| chart | best cell by IS score | IS | OOS | IS trades | cells positive in both halves |
| --- | --- | --- | --- | --- | --- |
| 15m | dip 48h, stop 2, trail 5 | -4.6% | +6.0% | 43 | 0 / 53 |
| 30m | breakout 120h, stop 2, trail 3 | +3.5% | -4.7% | 22 | 0 / 53 |
| 1h | rsi_meanrev 30 / 480h | -3.1% | -4.0% | 11 | 0 / 53 |

No cell on any chart is positive in both halves. The first seven months are a
CAKE downtrend (2.0 -> 1.35); long-only swing entries bleed there, and 15m
bleeds worst because 107 trades pay the spread every time.

## The one positive configuration, and why it is not a finding

Buy when RSI(14) crosses back above 30 **while price is above the 50-day EMA**,
stop 3 ATR, trail 5 ATR under the highest high since entry, out after 72h.

| chart | 12m | max dd | trades | wins | median trade | best 3 trades | in market |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | -9.9% | -24.5% | 107 | 36% | - | - | 12.0% |
| 30m | **+34.0%** | -14.3% | 47 | 40% | -0.51% | **99% of net P&L** | 12.6% |
| 1h | -3.5% | -12.2% | 16 | 25% | - | - | 6.5% |

30m is the only chart that paid, and it paid on three trades in the
August-September rally (+12.2%, +9.9%, +7.9%). The other 44 net roughly zero.
IS was +3.5% with 16 trades; the whole result is the OOS rally. That is a
position that happened to be long for an uptrend, not a repeatable edge.

### The gate is a plateau

| 30m gate | 20d | 35d | 50d | 70d | 100d |
| --- | --- | --- | --- | --- | --- |
| 12m return | +23.5% | +33.9% | +34.0% | +27.9% | +26.3% |
| max dd | -17.9% | -15.4% | -14.3% | -13.9% | -16.5% |

Every gate from 20 to 100 days ends positive; the gate keeps the book out of
the downtrend, which is most of the value.

### The exit is a cliff

| 30m exit | trail 3 | trail 4 | trail 5 | trail 6 | stop 2.5 | stop 4 |
| --- | --- | --- | --- | --- | --- | --- |
| 12m return | +10.8% | +12.4% | +34.0% | +22.2% | +36.5% | +32.3% |

A 20-point swing per ATR of trail is not a plateau, it is one lucky right tail.

## Cross-check on coarser charts

Same rule, 4h and 1d, two years (allowed for 4h/1d): **1-2 trades** at 4h
(-2.0% / -2.5%) and **0 trades** at 1d, against -49% for the coin. The dip
never coincides with the gate on a coarse chart, so the 30m result depends on
intrabar noise producing entries the 4h close smooths away.

## Verdict

- **30m is the answer to "which chart", not "ship this strategy".** It is the
  only chart where the entry rule found the rally at all; 15m loses to costs,
  1h is too slow to catch the dip (16 trades, ~flat).
- Not a shippable sleeve: it fails the IS screen, its P&L is 99% three trades,
  the exit is a cliff, and it has never seen a second CAKE rally.
- The honest next step is a paper forward test of `dip / 120h / 50-day gate /
  3 ATR stop / 5 ATR trail / 72h` on 30m at 3% risk, run beside the -36%
  buy-and-hold, and re-read after a rally it did not train on.
- What would change the verdict: the same rule positive on a second coin and a
  second CAKE rally, or an exit that is flat over 3-6 ATR instead of a spike.