"""JUP/USDT capitulation-dip sleeve: buy the crash, sell the bounce.

Fitted on JUP/USDT spot, 4h, 2025-11-28 .. 2026-11-28 (365 measured days,
next-open fills, 10 bps fee + 5 bps slippage a side, 3% risk, long only):

* entry: the close is at least 20% below the prior 60-bar high (240h; the high
  is shifted one bar so the signal bar cannot create its own reference);
* risk: 2.0 x ATR(14) below the signal close;
* target: 1.5R;
* time: close after 30 bars (120h) if neither stop nor target hit.

Measured: **+58.8%**, -7.9% max drawdown, 32 trades, 62.5% wins (95% Wilson
45-77%), profit factor 2.67, expectancy 0.50R, Sharpe 2.62, Sortino 5.14,
Calmar 7.49, in the market 9.7% of the year.  Halves +17.6% / +35.0%.
Daily-return correlation with the other four sleeves is -0.02 to +0.03, which
is the whole reason the five-sleeve book drew down only 7.3%.

**The honest caveat:** the cells one grid step away median only +8.7%, and the
same rule at the sweep's original 2.5 ATR stop made +28.1% - so the range for
this sleeve is roughly +9% to +59%, and this pick is the top of it.  It is the
best full-year Calmar of 216 JUP cells, fitted on the year it reports.  See
research/FIVE_SLEEVES.md before trusting the headline.

The event is a crash-and-rebound, so the sleeve sits in cash most of the time
and loses in a monotonic bleed.  It inherits the capitulation logic from the
UNI sleeve; only the numbers are JUP's.
"""

from __future__ import annotations

from .capitulation_dip_uni import CapitulationDipUni


class CapitulationDipJup(CapitulationDipUni):
    """JUP-tuned long of a 20% drawdown from the prior 60-bar high."""

    name = "capitulation_dip_jup"

    def __init__(
        self,
        window: int = 60,          # 240h
        drop_pct: float = 0.20,
        stop_atr: float = 2.0,
        rr: float | None = 1.5,
        max_hold: int = 30,        # 120h
        trail_atr: float | None = None,
        trail_after_r: float = 0.0,
    ) -> None:
        super().__init__(
            window=window,
            drop_pct=drop_pct,
            stop_atr=stop_atr,
            rr=rr,
            max_hold=max_hold,
            trail_atr=trail_atr,
            trail_after_r=trail_after_r,
        )
