"""Chart-structure gates (ported from the pasted pattern indicator).

Does any of it beat the deployed book?  Same engine (bot.book), same window,
long only, costs both sides.  Scratch research.
"""

from __future__ import annotations

import pandas as pd

from bot.book import BookConfig, load_bars_from_csv, simulate_book

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


def metrics(res, cash=1000.0):
    eq = res.equity
    dd = float((eq / eq.cummax() - 1.0).min())
    t = res.trades
    wins = float((t["pnl"] > 0).mean()) if len(t) else 0.0
    return res.final_equity / cash - 1.0, len(t), wins, dd, res.fees


def run(label, bars, **kw):
    cfg = BookConfig(**kw)
    ret, n, win, dd, fees = metrics(simulate_book(bars, cfg))
    print("%-26s ret %+8.1f%%  trades %4d  win %4.0f%%  dd %6.1f%%  fees $%6.0f"
          % (label, ret * 100, n, win * 100, dd * 100, fees))
    return ret


def main():
    bars = load_bars_from_csv("data/crypto", COINS, "4h")
    last = pd.Timestamp(max(max(b.ts for b in v) for v in bars.values()))
    start_1y = last - pd.Timedelta(days=365)
    bars_1y = {s: [b for b in v if b.ts >= start_1y] for s, v in bars.items()}

    base = dict(fast=30, slow=50, atr_mult=2.0, risk_per_trade=0.03,
                fee_bps=10.0, slippage_bps=5.0)
    gated = dict(base, damiani_filter=True, stc_min=50.0)

    for name, barset in (("FULL WINDOW", bars), ("LAST 12 MONTHS", bars_1y)):
        print()
        print("==", name, "==")
        run("plain", barset, **base)
        run("damiani+stc (book A)", barset, **gated)
        for label, extra in (
            ("breakout atr0.5 w3", dict(structure_gate="breakout", breakout_within=3)),
            ("breakout atr1.0 w3", dict(structure_gate="breakout", breakout_atr=1.0, breakout_within=3)),
            ("squeeze q0.4", dict(structure_gate="squeeze")),
            ("room rr2", dict(structure_gate="room", min_rr=2.0)),
        ):
            run("plain + " + label, barset, **base, **extra)
            run("gated + " + label, barset, **gated, **extra)


    print()
    print('== measured-move room, min_rr sweep (full window / last 12m) ==')
    for tag, cfg in (("plain", base), ("gated", gated)):
        for rr in (1.0, 1.5, 2.0, 3.0):
            row = []
            for barset in (bars, bars_1y):
                row.append(metrics(simulate_book(
                    barset, BookConfig(**cfg, structure_gate="room", min_rr=rr))))
            print('%-6s rr=%.1f  full %+7.1f%% (n=%3d, dd %5.1f%%)   1y %+7.1f%% (n=%3d, dd %5.1f%%)'
                  % (tag, rr, row[0][0] * 100, row[0][1], row[0][3] * 100,
                     row[1][0] * 100, row[1][1], row[1][3] * 100))

if __name__ == "__main__":
    main()