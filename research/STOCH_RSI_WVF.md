# Stochastic 14/3/5 + RSI flip + WVF: event study (2026-11-28)

> Scope: Binance spot 4h, last two years ending 2026-11-26, five symbols
> (BTC, ETH, BNB, SOL, XRP). Entry at the next bar open, 2 ATR stop,
> 1.5R target, 40-bar time exit, zero fees. These are event-study R
> multiples, not a shared-wallet equity curve.

## What was tested

The pasted Pine script is `ta.stoch(close, high, low, 14)` with SMA
smoothing `smoothK=3`, `smoothD=5`. The existing `bot.analysis.stochastic`
already exposes all three lengths. The user's additional condition is an RSI
oversold flip (previous RSI <= 30, current RSI > 30) alongside the Williams
Vix Fix first-green flash.

`wvfx_stoch` now has optional RSI parameters (off by default):
`rsi_window`, `rsi_oversold`, `rsi_overbought`, `rsi_flip_window`,
`require_rsi_flip`, `exit_rsi_overbought`.

## Results

| Rule | n | Win | E[R] | PF |
|---|---:|---:|---:|---:|
| WVF first green + stoch 14/1/3, oversold 5, cross up | 118 | 48.3% | +0.142 | 1.28 |
| Same, stoch 14/3/5 | 33 | 42.4% | -0.010 | 0.98 |
| 14/3/5 + RSI flip exact same bar | 0 | - | - | - |
| 14/3/5 + RSI flip within 5 bars | 3 | 33.3% | -0.167 | 0.75 |
| 14/1/3 + RSI flip exact | 4 | 75.0% | +0.719 | 3.88 |
| 14/1/3 + RSI flip within 5 | 10 | 50.0% | +0.154 | 1.31 |
| WVF first green + RSI flip within 5 (no stoch) | 64 | 48.4% | +0.159 | 1.32 |
| WVF first green + RSI flip exact (no stoch) | 8 | 62.5% | +0.484 | 2.29 |
| WVF first green only | 1,030 | 42.1% | +0.013 | 1.02 |
| RSI flip within 5 only (no WVF, no stoch) | 1,507 | 36.3% | -0.125 | 0.79 |

Per-symbol E[R] for the 14/3/5 WVF+stoch rule: BNB -0.40 (7 trades),
BTC -0.17 (3), ETH +0.50 (9), SOL -0.00 (10), XRP -0.37 (4). Only ETH was
positive, and its sample is nine trades. The same rule at 14/1/3 was positive
on four of five symbols (BNB was the exception), so the problem is the extra
smoothing, not the WVF idea.

Target-geometry check for the same 118 WVF+stoch events:

| Target | Win | E[R] |
|---|---:|---:|
| 0.5R | 72.9% | +0.093 |
| 1.0R | 53.4% | +0.074 |
| 1.5R | 48.3% | +0.142 |
| 2.0R | 45.8% | +0.171 |

## Reading

1. **14/3/5 is not a free improvement.** It cuts the WVF+stoch sample from
   118 to 33 and removes the per-trade edge (E[R] -0.01). The extra smoothing
   delays the cross past the panic bounce.
2. **RSI + WVF is the better pairing, not RSI + 14/3/5 + WVF.** WVF + RSI
   within 5 bars has 64 trades and E[R] +0.159; adding the smoothed stochastic
   leaves 3 trades.
3. **The exact same-bar RSI flip is too rare to tune on.** Eight WVF+RSI
   events won 62.5%, which sounds good but is not evidence of a 70% edge.
4. **70% win rate is a target-distance artifact here.** The base setup wins
   72.9% at 0.5R but earns only +0.093R per trade; at 1.5R it wins 48.3% but
   earns +0.142R. A headline hit rate without E[R] is not a strategy.
5. **RSI alone is negative.** The WVF panic context is what makes the RSI
   flip useful.

## How to run the optional confirmation

```text
python -m bot backtest --symbol BTC/USDT --timeframe 4h --strategy wvfx_stoch \
  --param smooth_k=3 --param smooth_d=5 --param require_rsi_flip=true \
  --param rsi_flip_window=5
```

Defaults remain the researched 14/1/3 setup. Test the RSI variants per symbol
before promoting any of them; the pooled result is not a per-symbol result.
