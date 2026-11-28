"""XRP/USDT WVF + stochastic reversal sleeve.

Researched on XRP/USDT spot, 4h, Binance production klines, 2024-11-28 ..
2026-11-28 (two years), next-open fills, 10 bps fee + 5 bps slippage a side,
3% risk, 95% max position, drawdown switch off, long only:

* WVF prints its first green bar of a panic cluster (WVF at/above its upper
  band or its rolling range high);
* stochastic %K crosses above %D while oversold (<20 at some point in the last
  5 bars);
* close is **below** the 200-day own-coin SMA - the panic must happen inside
  a downtrend, which is the risk filter, not an additional entry condition;
* stop: 2.0 x ATR(14) below the signal close;
* target: 2.0R;
* time exit: 240 bars (40 days), which is only a backstop - the longest
  actual hold was 368 hours (15 days), so it never bound;
* no trail.

Measured: **+48.9%**, -7.3% max drawdown, 19 trades, 63.2% wins, profit
factor 2.98, expectancy +0.86R; year 1 +7.3%, year 2 +38.8%. It held in 47 of
84 parameter neighbours; bootstrap profit-factor interval 1.21-9.30, the only
one of the five whose lower bound clears 1.0.

The target is the exit: 1.5R gives +36.2%, 2R +48.9%, 2.5R +46.5%, 3R +14.1%.
A parameter set belongs to one coin - do not reuse these numbers on another
symbol.
"""

from __future__ import annotations

from .wvfx_stoch import WilliamVixFixStoch


class WvfStochXrp(WilliamVixFixStoch):
    """XRP-tuned WVF panic reversal: 2R target, 2 ATR stop, 40-day backstop."""

    name = "wvf_stoch_xrp"

    def __init__(self) -> None:
        super().__init__(
            regime_days=200.0,
            stop_atr=2.0,
            rr=2.0,
            max_hold=240,
        )
