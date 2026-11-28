"""ENA/USDT breakout sleeve: a 120h breakout with a 3 ATR trailing stop.

Fitted and validated on ENA/USDT spot, 4h, Binance, next-open fills, 10 bps
fee + 5 bps slippage a side, 3% risk, 95% max position, drawdown switch off,
long only:

* entry: the close is above the highest high of the prior 30 bars (120h, the
  high is shifted one bar so the signal bar cannot create its own reference)
  and above the 120-bar EMA (480h);
* risk: 2 x ATR(14) below the signal close;
* exit: a 3 ATR Chandelier trail under the highest high since entry, or a
  close after 18 bars (72h), whichever comes first - no fixed target;
* in the market 5.7% of the year.

Measured 2025-11-28 .. 2026-11-28 (365 days): **+43.0%**, -16.6% max
drawdown, 24 trades, 37.5% wins (95% Wilson 21-57%), profit factor 1.98,
expectancy 0.60R, Sharpe 1.34, Sortino 3.49, Calmar 2.59.  Halves +5.9% /
+35.1%.  Bootstrap P(year > 0) = 83.2%.  Positive on 5 of 5 charts (1h +25.0%,
4h +43.0%, 6h +32.0%, 12h +49.2%, 1d +27.4%) and on all 12 parameter
neighbours (median +42.1%, worst +33.8%).

Two years of ENA, same rule: **+107.9% / -16.6%** (45 trades) on the evidence
pack's 730-day alignment, **+123.2% / -16.6%** (47 trades) on the sweep's - the
two differ by where a trade lands against the window's first bar, and both are
the same drawdown.  **ENA buy-and-hold over the same window lost 85.8%.**  The
one-year cell (+43.0% / -16.6% / 24 trades) is reproduced exactly by this class
through the research harness, which is the check that the sleeve is the studied
rule.

Why this one and not the shinier cells in the sweep: it is the only candidate
that was the best of all 26 configurations on the *earlier* year
(2024-11-28 .. 2025-11-27, +45.4%, Calmar-ranked) and then repeated in the
untouched year (+43.0%).  ENA also responds to 68% of its 26 cells, so the
coin is cooperative rather than one lucky cell.  The 1d variant of the same
rule re-fitted its best cell in the earlier year and lost it in the later one,
which is why this sleeve is 4h.  See research/BROAD_UNIVERSE.md.

A parameter set belongs to one coin - do not reuse these numbers on another
symbol, and do not tune them on the year that reports the result.
"""

from __future__ import annotations

from .swing_sleeve import SwingSleeve


class SwingBreakoutEna(SwingSleeve):
    """ENA-tuned 120h breakout, trailed, held for at most 72h."""

    name = "swing_breakout_ena"
    family = "breakout"
    lookback_bars = 30     # 120h
    trend_bars = 120       # 480h
    fast_bars = 6          # 24h (warm-up only for this family)
    stop_atr = 2.0
    rr = None              # trailing exit, no fixed target
    trail_atr = 3.0
    hold_bars = 18         # 72h