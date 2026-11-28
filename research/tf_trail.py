"""Timeframe + trailing-stop sweep (research).

Fits nothing: prints every cell on the first year and the last year separately,
so a timeframe that only works in one half is visible as such.
"""

from __future__ import annotations

import pandas as pd

from bot.book import BookConfig, simulate_book
from bot.scanlib import load_scan_bars

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
PLAN = {
    "4h": [(30, 50)],
    "6h": [(20, 33), (30, 50)],
    "1d": [(5, 8), (20, 50), (30, 50)],
}
TRAILS = [None, (2.0, 0.0), (3.0, 0.0), (3.0, 1.0)]


def metrics(res):
    eq = res.equity
    return (res.final_equity / res.config.initial_cash - 1.0, len(res.trades),
            float((eq / eq.cummax() - 1.0).min()), res.exposure)


def main():
    for tf, pairs in PLAN.items():
        bars = {s: load_scan_bars(s, tf, source="binance", days=730) for s in COINS}
        bars = {s: v for s, v in bars.items() if len(v) > 200}
        last = max(max(b.ts for b in v) for v in bars.values())
        mid = last - pd.Timedelta(days=365)
        train = {s: [b for b in v if b.ts < mid] for s, v in bars.items()}
        test = {s: [b for b in v if b.ts >= mid] for s, v in bars.items()}
        print()
        print("== %s  %s .. %s  (%d bars %s)" % (
            tf, min(v[0].ts for v in bars.values()).date(), last.date(),
            len(bars[COINS[0]]), tf))
        for fast, slow in pairs:
            for gate in (False, True):
                for trail in TRAILS:
                    kw = dict(fast=fast, slow=slow, damiani_filter=gate,
                              stc_min=50.0 if gate else None, risk_per_trade=0.03)
                    if trail:
                        kw["trail_atr"], kw["trail_after_r"] = trail
                    a = metrics(simulate_book(train, BookConfig(**kw)))
                    b = metrics(simulate_book(test, BookConfig(**kw)))
                    tag = "%d/%d %-4s %-12s" % (fast, slow,
                                                "gate" if gate else "plain",
                                                "trail %s@%sR" % trail if trail else "fixed stop")
                    print("%-34s yr1 %+7.1f%% dd %5.1f%% n=%3d exp %4.0f%%  |  yr2 %+7.1f%% dd %5.1f%% n=%3d exp %4.0f%%"
                          % (tag, a[0] * 100, a[2] * 100, a[1], a[3] * 100,
                             b[0] * 100, b[2] * 100, b[1], b[3] * 100))


if __name__ == "__main__":
    main()