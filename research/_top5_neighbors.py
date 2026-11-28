"""Local parameter neighbours for the top-5 candidates (scratch)."""
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
from _top5_scan import BAR_HOURS, LOAD_DAYS, run as eval_run  # noqa: E402

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
    # CAKE / SOL volume climax: the 4h defaults are 320h lookbacks and 192h hold.
    for sym in ("CAKE/USDT", "SOL/USDT", "BNB/USDT", "LINK/USDT", "FET/USDT"):
        for z in (2.5, 3.0, 3.5):
            for rr in (1.5, 2.0, 2.5):
                for drop in (0.05, 0.06, 0.07):
                    add(rows, sym, "4h", f"vol z{z:g} rr{rr:g} d{drop:.0%}",
                        build("vol_climax", vol_window=80, vol_z=z, lookback=80,
                              drop_pct=drop, close_pos=0.5, rr=rr,
                              stop_atr=0.5, max_hold=48))
        print(f"vol {sym} done", flush=True)

    # SUI breakout: 240h lookback, 480h regime, 72h hold.
    for sym in ("SUI/USDT",):
        for look in (168, 216, 240, 264, 288):
            for stop in (1.75, 2.0, 2.25):
                for trail in (2.5, 3.0, 4.0):
                    add(rows, sym, "4h",
                        f"bo {look}h s{stop:g} t{trail:g} h72",
                        SwingLab(family="breakout", lookback_hours=look,
                                 trend_hours=480, stop_atr=stop, rr=None,
                                 trail_atr=trail, max_hold_hours=72))
        for look in (168, 240, 288):
            for stop in (1.75, 2.0, 2.25):
                for trail in (2.5, 3.0, 4.0):
                    add(rows, sym, "4h",
                        f"bo {look}h s{stop:g} t{trail:g} h120",
                        SwingLab(family="breakout", lookback_hours=look,
                                 trend_hours=480, stop_atr=stop, rr=None,
                                 trail_atr=trail, max_hold_hours=120))
        print("SUI done", flush=True)

    # XRP / TAO daily breakout: 72h hold is only 3 daily bars, so test longer holds.
    for sym, look0 in (("XRP/USDT", 240), ("TAO/USDT", 120)):
        for look in (look0 - 48, look0, look0 + 48):
            for stop in (1.75, 2.0, 2.5):
                for trail in (3.0, 5.0):
                    for hold in (72, 240, 480, 720):
                        add(rows, sym, "1d",
                            f"bo {look}h s{stop:g} t{trail:g} h{hold}",
                            SwingLab(family="breakout", lookback_hours=look,
                                     trend_hours=480, stop_atr=stop, rr=None,
                                     trail_atr=trail, max_hold_hours=hold))
        print(f"daily {sym} done", flush=True)

    # XRP WVF alternate: keep the family fixed, vary the three exit knobs.
    for stop in (1.5, 2.0, 2.5):
        for rr in (2.0, 2.5, 3.0):
            for regime in (150.0, 200.0, 250.0):
                add(rows, "XRP/USDT", "4h", f"wvf s{stop:g} {rr:g}R {regime:g}d",
                    build("wvfx_stoch", stop_atr=stop, rr=rr, max_hold=240,
                          regime_days=regime))
    print("XRP WVF done", flush=True)

    d = pd.DataFrame(rows)
    out = HERE / "top5_neighbors.csv"
    d.to_csv(out, index=False)
    print(f"wrote {out} rows={len(d)}")
    ok = d[(d.train_ret > 0) & (d.test_ret > 0)]
    cols = ["symbol", "tf", "config", "train_ret", "train_dd", "train_trades",
            "test_ret", "test_dd", "test_trades", "test_pf", "full_ret", "full_dd"]
    for sym in d.symbol.unique():
        sub = ok[ok.symbol == sym].sort_values("test_ret", ascending=False)
        print(f"\n== {sym} robust variants ({len(sub)}/{len(d[d.symbol == sym])}) ==")
        print(sub[cols].head(12).to_string(index=False))


if __name__ == "__main__":
    main()
