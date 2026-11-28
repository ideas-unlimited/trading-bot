"""Does a per-coin winner come from fitting the year it reports?

The repo rule (docs/TARGETS.md, AGENTS.md) is: tune on the earlier slice,
validate on the later slice.  This script runs the full 26-cell grid on each
coin over *year A* (the 365 days before the measured window), picks the best
cell there, then reports what that same cell did in *year B* (the 365 days the
other scripts report).  It also reports the shared 1d breakout cell, which was
not fitted to any single coin.

    python research/broad_validate.py --pairs "ENA/USDT,QNT/USDT,ZEC/USDT"
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import BAR_HOURS, grids, run_one  # noqa: E402
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

FAMILY = "lab breakout 120h s2 t3"
MIN_TRADES = 8


def sweep(df: pd.DataFrame, tf: str, start_ts, end_ts) -> pd.DataFrame:
    sub = df[df["ts"] <= end_ts]
    bpy = 365.0 * 24.0 / BAR_HOURS[tf]
    rows = []
    for label, strat in grids(tf):
        if hasattr(strat, "reset"):
            strat.reset()
        try:
            m = run_one(strat, sub, bpy, start_ts)
        except Exception:
            continue
        dd = m.get("dd")
        m["calmar"] = (m["ret"] / abs(dd)) if isinstance(dd, float) and dd < 0 else float("nan")
        m["config"] = label
        rows.append(m)
    return pd.DataFrame(rows)


def main() -> None:
    pairs = ["ENA/USDT", "QNT/USDT", "ZEC/USDT", "TAO/USDT", "SUI/USDT"]
    if "--pairs" in sys.argv:
        pairs = sys.argv[sys.argv.index("--pairs") + 1].split(",")
    out = []
    for pair in pairs:
        for tf in ("4h", "1d"):
            try:
                bars = load_scan_bars(pair, tf, source="binance", days=730)
            except Exception as exc:
                print(f"  skip {pair} {tf}: {exc}")
                continue
            df = bars_to_frame(bars)
            last = df["ts"].iloc[-1]
            b_end = last
            b_start = last - pd.Timedelta(days=365)
            a_end = b_start - pd.Timedelta(days=1)
            a_start = a_end - pd.Timedelta(days=365)
            a = sweep(df, tf, a_start, a_end)
            a = a[(a.trades >= MIN_TRADES) & a.ret.notna()]
            if a.empty:
                continue
            best = a.sort_values("calmar", ascending=False).iloc[0]
            b = sweep(df, tf, b_start, b_end)
            b = b[b.trades >= MIN_TRADES].set_index("config")
            fam_a = a[a.config == FAMILY]
            fam_b = b.loc[FAMILY] if FAMILY in b.index else None
            out.append(dict(
                pair=pair, tf=tf,
                curve_a_best=best.config, a_ret=best.ret, a_dd=best.dd,
                a_calmar=best.calmar, a_trades=best.trades,
                b_ret_for_a_best=float(b.loc[best.config, "ret"]) if best.config in b.index else float("nan"),
                b_trades_for_a_best=int(b.loc[best.config, "trades"]) if best.config in b.index else 0,
                family_a_ret=float(fam_a.ret.iloc[0]) if not fam_a.empty else float("nan"),
                family_a_trades=int(fam_a.trades.iloc[0]) if not fam_a.empty else 0,
                family_b_ret=float(fam_b.ret) if fam_b is not None else float("nan"),
                family_b_trades=int(fam_b.trades) if fam_b is not None else 0,
            ))
            r = out[-1]
            print(f"  {pair:11} {tf:2} A-best {best.config:<26} A {r['a_ret']:+7.1%} "
                  f"B {r['b_ret_for_a_best']:+7.1%} | family A {r['family_a_ret']:+7.1%} "
                  f"B {r['family_b_ret']:+7.1%}", flush=True)
    d = pd.DataFrame(out)
    d.to_csv(HERE / "broad_validate.csv", index=False)
    pd.set_option("display.width", 240)
    t = d.copy()
    for c in ("a_ret", "b_ret_for_a_best", "family_a_ret", "family_b_ret"):
        t[c] = t[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    t["a_calmar"] = t["a_calmar"].map(lambda v: "" if pd.isna(v) else f"{v:+.2f}")
    print(t[["pair", "tf", "curve_a_best", "a_ret", "b_ret_for_a_best", "family_a_ret", "family_b_ret"]].to_string(index=False))
    ok = d[(d.a_ret > 0) & (d.b_ret_for_a_best > 0)]
    print(f"\nfit-on-A kept its sign in B: {len(ok)}/{len(d)}")
    fam = d[(d.family_a_ret > 0) & (d.family_b_ret > 0)]
    print(f"shared 1d breakout positive in both years: {len(fam)}/{len(d)}")


if __name__ == "__main__":
    main()