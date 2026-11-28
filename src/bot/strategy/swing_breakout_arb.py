"""ARB/USDT breakout sleeve: the same rule with a longer 168h lookback.

Fitted on ARB/USDT spot, 4h, 2025-11-28 .. 2026-11-28 (365 measured days,
next-open fills, 10 bps fee + 5 bps slippage a side, 3% risk, long only):

* entry: close above the highest high of the prior 42 bars (168h, shifted one
  bar) and above the 120-bar EMA (480h);
* risk: 1.5 x ATR(14) below the signal close;
* target: 2R;
* time: close after 18 bars (72h).

Measured: **+67.2%**, -8.4% max drawdown, 30 trades, 56.7% wins (95% Wilson
39-73%), profit factor 2.33, expectancy 0.59R, Sharpe 2.60, Sortino 6.62,
Calmar 7.99, in the market 5.1% of the year.  Halves +10.1% / +51.8% - almost
all of the year came in the second half, which is the honest weakness of this
sleeve.  Neighbour median +37.8%; bootstrap P(year > 0) = 97.5%.

Best full-year Calmar of 224 ARB cells, so fitted on the reported year.  It is
a separate class from the RENDER sleeve even though the logic is shared: the
lookback is 42 bars here and 30 there, and a parameter set belongs to one coin.
See research/FIVE_SLEEVES.md.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutArb(SwingSleeve):
    """ARB-tuned 168h breakout held for at most 72h."""

    name = "swing_breakout_arb"
    family = "breakout"
    lookback_bars = 42     # 168h
    trend_bars = 120       # 480h
    fast_bars = 6          # 24h (warm-up only for this family)
    stop_atr = 1.5
    rr = 2.0
    hold_bars = 18         # 72h
