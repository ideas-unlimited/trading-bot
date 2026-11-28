"""RENDER/USDT breakout sleeve: buy a 30-bar high that is also above trend.

Fitted on RENDER/USDT spot, 4h, 2025-11-28 .. 2026-11-28 (365 measured days,
next-open fills, 10 bps fee + 5 bps slippage a side, 3% risk, long only):

* entry: the close is above the highest high of the prior 30 bars (120h, the
  high is shifted one bar so the signal bar cannot create its own reference)
  and above the 120-bar EMA (480h);
* risk: 1.5 x ATR(14) below the signal close;
* target: 2R;
* time: close after 18 bars (72h) if neither stop nor target hit.

Measured: **+70.1%**, -11.9% max drawdown, 34 trades, 55.9% wins (95% Wilson
39-71%), profit factor 2.24, expectancy 0.54R, Sharpe 2.31, Sortino 5.54,
Calmar 5.90, in the market 7.5% of the year.  Halves +47.3% / +15.5%.
Neighbour median +32.7%; bootstrap P(year > 0) = 98.4%.

It is the best full-year Calmar of 224 RENDER cells, so it is fitted on the
year it reports; the neighbour median is the check that it is not a spike.
See research/FIVE_SLEEVES.md.  Do not reuse these numbers on another coin.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutRender(SwingSleeve):
    """RENDER-tuned 120h breakout held for at most 72h."""

    name = "swing_breakout_render"
    family = "breakout"
    lookback_bars = 30     # 120h
    trend_bars = 120       # 480h
    fast_bars = 6          # 24h (warm-up only for this family)
    stop_atr = 1.5
    rr = 2.0
    hold_bars = 18         # 72h
