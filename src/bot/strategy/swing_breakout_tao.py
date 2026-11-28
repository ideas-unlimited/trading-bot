"""TAO/USDT breakout sleeve: 120h high, 2 ATR stop, 3-day cap.

Researched on TAO/USDT spot, 1d, Binance production klines, 2024-11-28 ..
2026-11-28 (two years), next-open fills, 10 bps fee + 5 bps slippage a side,
3% risk, 95% max position, drawdown switch off, long only:

* entry: close above the highest high of the prior 5 bars (120h; shifted one
  bar) and above the 20-bar EMA (480h);
* risk: 2.0 x ATR(14) below the signal close;
* exit: a 3 ATR Chandelier trail under the highest high since entry, or a
  close after 3 bars (72h), whichever comes first - no fixed target.

Measured: **+21.6%**, -5.6% max drawdown, 32 trades, 59.4% wins, profit
factor 1.93, expectancy +0.23R; year 1 +5.8%, year 2 +14.9%. This is the
weakest plateau in the five: only 8 of 72 parameter neighbours held.

The trail is inert in this sample - the 3 ATR trail never fires before the
3-day clock, and stop + time alone is identical (+21.6%). The clock is the
exit; a fixed 2R target with the clock is +18.5%, and without the clock the
trail returns -7.7%. A parameter set belongs to one coin - do not reuse these
numbers on another symbol.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutTao(SwingSleeve):
    """TAO-tuned 120h breakout, trailed at 3 ATR, held at most 72h."""

    name = "swing_breakout_tao"
    family = "breakout"
    lookback_bars = 5       # 120h
    trend_bars = 20         # 480h
    fast_bars = 2           # 48h (warm-up only for this family)
    stop_atr = 2.0
    rr = None               # trailing exit, no fixed target
    trail_atr = 3.0
    hold_bars = 3           # 72h
