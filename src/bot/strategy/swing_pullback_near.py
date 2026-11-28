"""NEAR/USDT pullback sleeve: buy a dip to the 6-bar EMA inside an uptrend.

Fitted on NEAR/USDT spot, 4h, 2025-11-28 .. 2026-11-28 (365 measured days,
455 loaded so the warm-up burns outside the window, Binance production klines,
next-open fills, 10 bps fee + 5 bps slippage each side, 3% of equity at risk,
long only, account drawdown switch off):

* entry: the prior bar's low touched the 6-bar EMA (24h), the close is back
  above it and above the prior close, and the close is above the 120-bar EMA
  (480h, the uptrend filter);
* risk: 2.0 x ATR(14) below the signal close;
* target: 2R;
* time: close after 30 bars (120h) if neither stop nor target hit.

Measured: **+86.0%**, -12.9% max drawdown, 61 trades, 49.2% wins (95% Wilson
37-61%), profit factor 1.73, expectancy 0.36R, Sharpe 2.23, Sortino 4.40,
Calmar 6.69, in the market 19.2% of the year.  Halves +50.2% / +23.9%.  The
cells one grid step away median +31.0%, so this is a plateau rather than a
spike, and a 5,000-draw trade-order bootstrap gives P(year > 0) = 97.2%.

The pick is the best full-year Calmar out of 144 NEAR cells, i.e. it is fitted
on the year it reports; see research/FIVE_SLEEVES.md for the whole table, the
halves and why one year is not a promise.  This sleeve must not be run on
another coin: the numbers were fitted on NEAR and each coin gets its own.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingPullbackNear(SwingSleeve):
    """NEAR-tuned pullback to a fast EMA inside a trend."""

    name = "swing_pullback_near"
    family = "pullback"
    fast_bars = 6          # 24h
    trend_bars = 120       # 480h
    lookback_bars = 12     # unused by the pullback family
    stop_atr = 2.0
    rr = 2.0
    hold_bars = 30         # 120h
