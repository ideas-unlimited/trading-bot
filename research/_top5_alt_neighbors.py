"""Neighbours for the alternate fifth-pick candidates (scratch)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE / "cake"))
sys.path.insert(0, str(HERE))

from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from cake_swing import SwingLab  # noqa: E402
from _top5_scan import LOAD_DAYS, run as eval_run  # noqa: E402

CACHE: dict[tuple[str, str], list] = {}


def bars(sym: str, tf: str):
    key = (sym, tf)
    if key not in CACHE:
        CACHE[key] = load_scan_bars(sym, tf, source="binance", days=LOAD_DAYS)
    return CACHE[key]


def add(rows, sym, tf, label, strat):
    try:
        m = eval_run(sym, tf, label, strat, bars(sym, tf))
    except Exception as exc:
        print(f"!! {sym} {tf} {label}: {exc}", flush=True)
        return
    if m:
        rows.append(m)


def main():
    rows = []
    # INJ daily breakout: strongest alternate in the 1d scan.
    for look in (96, 120, 144, 168):
        for stop in (1.75, 2.0, 2.25):
            for trail in (3.0, 5.0):
                for hold in (72, 240, 480):
                    add(rows, "INJ/USDT", "1d", f"bo {look}h s{stop:g} t{trail:g} h{hold}",
                        SwingLab(family="breakout", lookback_hours=look,
                                 trend_hours=480, stop_atr=stop, rr=None,
                                 trail_atr=trail, max_hold_hours=hold))
    print("INJ done", flush=True)

    # LINK daily capitulation dip: the only 1d mean-reversion candidate with 18 train trades.
    for win in (10, 15, 20):
        for drop in (0.15, 0.20, 0.25):
            for stop in (1.5, 2.0, 2.5):
                for rr in (1.5, 2.0):
                    for hold in (240, 480):
                        add(rows, "LINK/USDT", "1d",
                            f"capdip w{win} d{drop:.0%} s{stop:g} rr{rr:g} h{hold}",
                            build("capitulation_dip_uni", window=win, drop_pct=drop,
                                  stop_atr=stop, rr=rr, max_hold=hold))
    print("LINK done", flush=True)

    # AVAX daily breakout: 13 train / 10 test, but weak train.
    for look in (168, 240, 288):
        for stop in (1.75, 2.0, 2.25):
            for trail in (3.0, 5.0):
                add(rows, "AVAX/USDT", "1d", f"bo {look}h s{stop:g} t{trail:g} h72",
                    SwingLab(family="breakout", lookback_hours=look,
                             trend_hours=480, stop_atr=stop, rr=None,
                             trail_atr=trail, max_hold_hours=72))
    print("AVAX done", flush=True)

    # ETH daily pullback: many trades, weak test.
    for look in (168, 240, 288):
        for stop in (2.0, 2.5, 3.0):
            for rr in (2.0, 3.0):
                add(rows, "ETH/USDT", "1d", f"pull {look}h s{stop:g} {rr:g}R h480",
                    SwingLab(family="pullback", lookback_hours=look,
                             trend_hours=480, fast_hours=120, stop_atr=stop,
                             rr=rr, trail_atr=None, max_hold_hours=480))
    print("ETH done", flush=True)

    # BNB daily momentum: low sample, but test its plateau.
    for look in (96, 120, 168):
        for regime in (20, 40, 60):
            for trail in (3.0, 3.5, 5.0):
                add(rows, "BNB/USDT", "1d", f"mom {look}h r{regime} t{trail:g}",
                    build("momentum_swing", lookback=look, regime_ma=regime,
                          atr_mult=2.5, trail_atr=trail, breakout=0.02))
    print("BNB done", flush=True)

    d = pd.DataFrame(rows)
    out = HERE / "top5_alt_neighbors.csv"
    d.to_csv(out, index=False)
    print(f"wrote {out} rows={len(d)}")
    d["worst"] = d[["train_ret", "test_ret"]].min(axis=1)
    ok = d[(d.train_ret > 0) & (d.test_ret > 0)]
    cols = ["symbol", "tf", "config", "train_ret", "train_dd", "train_trades",
            "test_ret", "test_dd", "test_trades", "test_pf", "full_ret", "full_dd", "worst"]
    for sym in d.symbol.unique():
        sub = ok[ok.symbol == sym].sort_values("worst", ascending=False)
        print(f"\n== {sym} robust {len(sub)}/{len(d[d.symbol == sym])} ==")
        print(sub[cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
