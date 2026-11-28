"""Per-symbol tuning, with year 2 kept as a real holdout (research).

For each coin: fit (fast, slow, trail) on year 1 by Calmar, subject to a
drawdown floor and a minimum trade count, then score that config on year 2
and compare it with the pooled 30/50 + trail 1.5 config used by the book.
"""
from __future__ import annotations

import itertools

import pandas as pd

from bot.book import BookConfig, simulate_book, summarize
from bot.scanlib import load_scan_bars

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
PAIRS = [(15, 30), (20, 40), (30, 50), (40, 80)]
TRAILS = [None, 1.0, 1.5, 2.0, 2.5, 3.0]


def config(fast, slow, trail, risk=0.03, gate=True):
    kw = dict(fast=fast, slow=slow, risk_per_trade=risk, max_positions=1)
    if gate:
        kw.update(damiani_filter=True, stc_min=50.0)
    if trail is not None:
        kw["trail_atr"] = trail
    return BookConfig(**kw)


def main():
    print("%-9s %-26s %-24s %-24s" % ("symbol", "fitted on year 1", "year 1", "year 2 (holdout)"))
    wins = 0
    for sym in COINS:
        bars = load_scan_bars(sym, "4h", source="binance", days=730)
        last = max(b.ts for b in bars)
        mid = last - pd.Timedelta(days=365)
        y1 = [b for b in bars if b.ts < mid]
        y2 = [b for b in bars if b.ts >= mid]
        rows = []
        for (fast, slow), trail in itertools.product(PAIRS, TRAILS):
            cfg = config(fast, slow, trail)
            st = summarize(simulate_book({sym: y1}, cfg))
            if st["trades"] < 8 or st["max_drawdown"] < -0.30:
                continue
            rows.append((st["calmar"], fast, slow, trail, st))
        rows.sort(reverse=True)
        best = rows[0]
        pool = config(30, 50, 1.5)
        p1 = summarize(simulate_book({sym: y1}, pool))
        p2 = summarize(simulate_book({sym: y2}, pool))
        b2 = summarize(simulate_book({sym: y2}, config(best[1], best[2], best[3])))
        tag = "%d/%d trail %s" % (best[1], best[2], "fixed" if best[3] is None else "%.1f" % best[3])
        print("%-9s %-26s %+6.1f%% dd %5.1f%% n %2.0f | %+6.1f%% dd %5.1f%% (pooled %+6.1f%%)"
              % (sym, tag, best[4]["total_return"] * 100, best[4]["max_drawdown"] * 100,
                 best[4]["trades"], b2["total_return"] * 100, b2["max_drawdown"] * 100,
                 p2["total_return"] * 100))
        if b2["total_return"] > p2["total_return"]:
            wins += 1
    print()
    print("per-symbol fit beat the pooled config on the holdout for %d of %d coins" % (wins, len(COINS)))


if __name__ == "__main__":
    main()