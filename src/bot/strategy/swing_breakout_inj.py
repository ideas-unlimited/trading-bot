"""INJ/USDT breakout sleeve: 168h high, 1.75 ATR stop, 3-day cap.

Researched on INJ/USDT spot, 1d, Binance production klines, 2024-11-28 ..
2026-11-28 (two years), next-open fills, 10 bps fee + 5 bps slippage a side,
3% risk, 95% max position, drawdown switch off, long only:

* entry: close above the highest high of the prior 7 bars (168h; shifted one
  bar) and above the 20-bar EMA (480h);
* risk: 1.75 x ATR(14) below the signal close;
* exit: a 3 ATR Chandelier trail under the highest high since entry, or a
  close after 3 bars (72h), whichever comes first - no fixed target.

Measured: **+23.5%**, -5.3% max drawdown, 27 trades, 66.7% wins, profit
factor 2.38, expectancy +0.29R; year 1 +6.2%, year 2 +16.2%. It held in 18 of
72 parameter neighbours.

The 3-day clock is the load-bearing exit: without it the same trail returns
-6.7%. The trail adds 2.3 points over stop + time (+21.1%). A fixed 2R target
with the clock is +20.1%, so capping the move at 2R gives up part of the edge.
A parameter set belongs to one coin - do not reuse these numbers on another
symbol.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutInj(SwingSleeve):
    """INJ-tuned 168h breakout, trailed at 3 ATR, held at most 72h."""

    name = "swing_breakout_inj"
    family = "breakout"
    lookback_bars = 7       # 168h
    trend_bars = 20         # 480h
    fast_bars = 2           # 48h (warm-up only for this family)
    stop_atr = 1.75
    rr = None               # trailing exit, no fixed target
    trail_atr = 3.0
    hold_bars = 3           # 72h
