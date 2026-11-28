"""Tune the measured-move "room" gate: fit on year 1, validate on year 2.

Walk-forward on purpose: the parameters are chosen on the first 12 months
and only then measured on the last 12, so the validation column is not a
number the search ever saw.  Scratch research.
"""

from __future__ import annotations

import itertools

import pandas as pd

from bot.book import BookConfig, load_bars_from_csv, simulate_book

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


def metrics(res):
    eq = res.equity
    return (res.final_equity / 1000.0 - 1.0, len(res.trades),
            float((eq / eq.cummax() - 1.0).min()))


def slice_bars(bars, lo, hi):
    return {s: [b for b in v if lo <= b.ts < hi] for s, v in bars.items()}


def main():
    bars = load_bars_from_csv("data/crypto", COINS, "4h")
    last = pd.Timestamp(max(max(b.ts for b in v) for v in bars.values()))
    mid = last - pd.Timedelta(days=365)
    train = slice_bars(bars, last - pd.Timedelta(days=730), mid)
    test = slice_bars(bars, mid, last + pd.Timedelta(seconds=1))

    rows = []
    for span, rr in itertools.product((5, 10, 15), (1.2, 1.5, 1.8, 2.0, 2.5, 3.0)):
        cfg = BookConfig(fast=30, slow=50, atr_mult=2.0, risk_per_trade=0.03,
                         structure_gate="room", min_rr=rr,
                         channel_left=span, channel_right=span)
        tr, tt = metrics(simulate_book(train, cfg)), metrics(simulate_book(test, cfg))
        rows.append((span, rr, *tr, *tt))

    base = metrics(simulate_book(train, BookConfig()))
    base2 = metrics(simulate_book(test, BookConfig()))
    print("baseline plain 30/50        train %+7.1f%% (n=%3d dd %5.1f%%)  test %+7.1f%% (n=%3d dd %5.1f%%)"
          % (base[0] * 100, base[1], base[2] * 100, base2[0] * 100, base2[1], base2[2] * 100))
    print()
    print("%-5s %-5s | %-28s | %-28s" % ("span", "rr", "TRAIN (fit)", "TEST (validate)"))
    for span, rr, a, na, da, b, nb, db in sorted(rows, key=lambda r: -r[5]):
        print("%-5d %-5.1f | %+7.1f%% n=%3d dd %5.1f%%     | %+7.1f%% n=%3d dd %5.1f%%"
              % (span, rr, a * 100, na, da * 100, b * 100, nb, db * 100))


if __name__ == "__main__":
    main()