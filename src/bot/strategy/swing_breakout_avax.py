"""AVAX/USDT breakout sleeve: 288h high, 2 ATR stop, 3-day cap.

Researched on AVAX/USDT spot, 1d, Binance production klines, 2024-11-28 ..
2026-11-28 (two years), next-open fills, 10 bps fee + 5 bps slippage a side,
3% risk, 95% max position, drawdown switch off, long only:

* entry: close above the highest high of the prior 12 bars (288h; shifted
  one bar) and above the 20-bar EMA (480h);
* risk: 2.0 x ATR(14) below the signal close;
* exit: a 3 ATR Chandelier trail under the highest high since entry, or a
  close after 3 bars (72h), whichever comes first - no fixed target.

Measured: **+13.3%**, -6.1% max drawdown, 20 trades, 50% wins, profit factor
1.79, expectancy +0.22R; year 1 +5.6%, year 2 +7.3%. It held in 16 of 18
parameter neighbours, but one trade produced 92% of the P&L - the return is
less diversified than the other four.

The trail is inert in this sample - stop + 3-day time alone is identical
(+13.3%). The clock is the exit; a fixed 2R target with the clock returns
+7.5%, and without the clock the trail loses -13.9%. A parameter set belongs
to one coin - do not reuse these numbers on another symbol.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutAvax(SwingSleeve):
    """AVAX-tuned 288h breakout, trailed at 3 ATR, held at most 72h."""

    name = "swing_breakout_avax"
    family = "breakout"
    lookback_bars = 12      # 288h
    trend_bars = 20         # 480h
    fast_bars = 2           # 48h (warm-up only for this family)
    stop_atr = 2.0
    rr = None               # trailing exit, no fixed target
    trail_atr = 3.0
    hold_bars = 3           # 72h
