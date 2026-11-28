"""SUI/USDT breakout sleeve: 240h high, 2.5 ATR trail, 3-day cap.

Researched on SUI/USDT spot, 4h, Binance production klines, 2024-11-28 ..
2026-11-28 (two years), next-open fills, 10 bps fee + 5 bps slippage a side,
3% risk, 95% max position, drawdown switch off, long only:

* entry: close above the highest high of the prior 60 bars (240h; the high is
  shifted one bar, so the signal bar cannot create its own reference) and
  above the 120-bar EMA (480h);
* risk: 2.0 x ATR(14) below the signal close;
* exit: a 2.5 ATR Chandelier trail under the highest high since entry, or a
  close after 18 bars (72h), whichever comes first - no fixed target.

Measured: **+57.8%**, -13.1% max drawdown, 31 trades, 41.9% wins, profit
factor 2.27, expectancy +0.62R; year 1 +24.9%, year 2 +26.3%. The rule was
positive on 71 of 72 parameter neighbours, the strongest plateau in the five.
The trail is the profit engine here: removing the time exit still returns
+55.3%, while a fixed 2R target cuts the result to +28.3%.

Both `bot forward` and `bot live` now run the same 2.5 ATR trail: the live
loop persists the peak and ratchets the local stop after each closed bar's
exit check, so a restart cannot reset or lower it. A parameter set belongs
to one coin - do not reuse these numbers on another symbol.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutSui(SwingSleeve):
    """SUI-tuned 240h breakout, trailed at 2.5 ATR, held at most 72h."""

    name = "swing_breakout_sui"
    family = "breakout"
    lookback_bars = 60      # 240h
    trend_bars = 120        # 480h
    fast_bars = 6           # 24h (warm-up only for this family)
    stop_atr = 2.0
    rr = None               # trailing exit, no fixed target
    trail_atr = 2.5
    hold_bars = 18          # 72h
