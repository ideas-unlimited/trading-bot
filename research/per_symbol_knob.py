"""One knob per symbol: does even a single per-symbol parameter survive? (research)"""
from __future__ import annotations

import pandas as pd

from bot.book import BookConfig, simulate_book, summarize
from bot.scanlib import load_scan_bars

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TRAILS = [None, 1.0, 1.5, 2.0, 2.5, 3.0]


def cfg(trail):
    kw = dict(fast=30, slow=50, risk_per_trade=0.03, max_positions=1,
              damiani_filter=True, stc_min=50.0)
    if trail is not None:
        kw["trail_atr"] = trail
    return BookConfig(**kw)


def main():
    wins = 0
    print("%-9s %-18s %-22s %-22s %s" % ("symbol", "trail picked y1", "year 1", "year 2", "pooled 1.5 y2"))
    for sym in COINS:
        bars = load_scan_bars(sym, "4h", source="binance", days=730)
        last = max(b.ts for b in bars)
        mid = last - pd.Timedelta(days=365)
        y1 = [b for b in bars if b.ts < mid]
        y2 = [b for b in bars if b.ts >= mid]
        scored = [(summarize(simulate_book({sym: y1}, cfg(t))), t) for t in TRAILS]
        scored = [(st, t) for st, t in scored if st["trades"] >= 6]
        best, pick = max(scored, key=lambda pair: pair[0]["calmar"])
        h = summarize(simulate_book({sym: y2}, cfg(pick)))
        pooled = summarize(simulate_book({sym: y2}, cfg(1.5)))
        print("%-9s %-18s %+7.1f%% dd %5.1f%% n %2.0f | %+7.1f%% dd %5.1f%% | %+7.1f%%"
              % (sym, "fixed" if pick is None else "%.1f ATR" % pick,
                 best["total_return"] * 100, best["max_drawdown"] * 100, best["trades"],
                 h["total_return"] * 100, h["max_drawdown"] * 100, pooled["total_return"] * 100))
        wins += h["total_return"] > pooled["total_return"]
    print()
    print("one-knob per-symbol fit beat the pooled trail on the holdout for %d of %d coins" % (wins, len(COINS)))


if __name__ == "__main__":
    main()