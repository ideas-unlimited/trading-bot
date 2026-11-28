"""Stage 2 of the all-coin study: are the stage-1 winners real?

Takes the top cells from allcoins_stage1.csv and asks the two questions that
separate a finding from a curve fit:

  1. cross-chart - does the same economic config still work when the chart
     changes (1h / 4h / 6h / 12h / 1d)? A cell that only exists at one chart is
     a coincidence of that chart.
  2. neighbours - perturb every numeric knob by -20% / +25% and re-run. A real
     plateau keeps most of its return; a spike collapses.

    python research/allcoins_deep.py [--top 8]
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import (  # noqa: E402
    BAR_HOURS, LOAD_DAYS, WINDOW_DAYS, grids, run_one,
)
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

TFS = ["1h", "4h", "6h", "12h", "1d"]
MIN_TRADES = 10


def cell_map(tf: str) -> dict[str, object]:
    return {label: strat for label, strat in grids(tf)}


def numeric_params(strat) -> dict[str, float | int]:
    out = {}
    for p in getattr(strat, "PARAMS", ()):
        v = getattr(strat, p, None)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            out[p] = v
    return out


def rebuild(strat, overrides: dict) -> object | None:
    params = {p: getattr(strat, p) for p in getattr(strat, "PARAMS", ())}
    params.update(overrides)
    try:
        return type(strat)(**params)
    except Exception:
        return None


def main() -> None:
    top = 8
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    d = pd.read_csv(HERE / "allcoins_stage1.csv")
    ok = d[(d.trades >= MIN_TRADES) & d.ret.notna()]
    picks = []

    def add(row):
        if (row.symbol, row.config) in [(p["symbol"], p["config"]) for p in picks]:
            return
        picks.append(dict(symbol=row.symbol, config=row.config, base_tf=row.tf,
                          base_ret=float(row.ret), base_dd=float(row.dd),
                          base_trades=int(row.trades), base_is=float(row.is_ret),
                          base_oos=float(row.oos_ret), base_score=float(row.score),
                          bh=float(row.bh)))

    if "--picks" in sys.argv:
        for spec in sys.argv[sys.argv.index("--picks") + 1].split(","):
            sym, label = spec.split("|", 1)
            hit = ok[(ok.symbol == sym) & (ok.config == label)]
            if hit.empty:
                raise SystemExit(f"no stage-1 row for {spec}")
            add(hit.iloc[0])
    else:
        for sub in (ok[ok.score.notna()].sort_values("score", ascending=False).head(top),
                    ok.sort_values("ret", ascending=False).head(top)):
            for _, r in sub.iterrows():
                add(r)
    print(f"{len(picks)} finalists")

    # reproducibility guard: rebuild one known cell and compare
    chk = picks[0]
    bars = load_scan_bars(chk["symbol"], chk["base_tf"], source="binance", days=LOAD_DAYS)
    df = bars_to_frame(bars)
    start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    strat = cell_map(chk["base_tf"]).get(chk["config"])
    if strat is None:
        raise SystemExit(f"label drift: {chk['config']} not in stage-1 grid")
    got = run_one(strat, df, 365.0 * 24.0 / BAR_HOURS[chk["base_tf"]], start_ts)["ret"]
    if abs(got - chk["base_ret"]) > 1e-6:
        raise SystemExit(f"stage 1 not reproducible on {chk}: {got} vs {chk['base_ret']}")
    print("stage 1 reproduces", flush=True)

    rows = []
    for p in picks:
        tf_res = {}
        base_strat = None
        for tf in TFS:
            strat = cell_map(tf).get(p["config"])
            if strat is None:
                continue
            try:
                bars = load_scan_bars(p["symbol"], tf, source="binance", days=LOAD_DAYS)
            except Exception as exc:
                print(f"  skip {p['symbol']} {tf}: {exc}", flush=True)
                continue
            if len(bars) < 200:
                continue
            df = bars_to_frame(bars)
            start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
            m = run_one(strat, df, 365.0 * 24.0 / BAR_HOURS[tf], start_ts)
            tf_res[tf] = dict(ret=m["ret"], trades=m["trades"], dd=m["dd"],
                              is_ret=m["is_ret"], oos_ret=m["oos_ret"])
            if tf == p["base_tf"]:
                base_strat = strat
        # neighbours on the base chart
        neigh = []
        if base_strat is not None:
            knobs = numeric_params(base_strat)
            bars = load_scan_bars(p["symbol"], p["base_tf"], source="binance", days=LOAD_DAYS)
            df = bars_to_frame(bars)
            start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
            for name, val in list(knobs.items())[:6]:
                for factor in (0.8, 1.25):
                    new = val * factor
                    if isinstance(val, int):
                        new = max(1, int(round(new)))
                    variant = rebuild(base_strat, {name: new})
                    if variant is None:
                        continue
                    try:
                        m = run_one(variant, df, 365.0 * 24.0 / BAR_HOURS[p["base_tf"]], start_ts)
                    except Exception:
                        continue
                    neigh.append(dict(knob=name, value=new, ret=m["ret"], trades=m["trades"]))
        p["tfs"] = tf_res
        p["tfs_positive"] = sum(1 for v in tf_res.values() if v["ret"] > 0)
        p["tfs_tested"] = len(tf_res)
        rets = [n["ret"] for n in neigh if n["trades"] >= 5]
        p["neigh_n"] = len(rets)
        p["neigh_median"] = float(pd.Series(rets).median()) if rets else float("nan")
        p["neigh_min"] = float(min(rets)) if rets else float("nan")
        p["neigh_max"] = float(max(rets)) if rets else float("nan")
        p["neigh_share_pos"] = (sum(1 for r in rets if r > 0) / len(rets)) if rets else float("nan")
        rows.append(p)
        print(f"  {p['symbol']:>10} {p['config']:<28} "
              f"base {p['base_ret']:+7.1%} tf+ {p['tfs_positive']}/{p['tfs_tested']} "
              f"neigh med {p['neigh_median']:+7.1%}", flush=True)

    out = pd.DataFrame(rows)
    out["tfs"] = out["tfs"].map(json.dumps)
    name = "allcoins_stage2_picks.csv" if "--picks" in sys.argv else "allcoins_stage2.csv"
    out.to_csv(HERE / name, index=False)
    print(f"\nwrote {HERE / name}")

    show = out.sort_values(["tfs_positive", "neigh_median"], ascending=False)
    cols = ["symbol", "config", "base_tf", "base_ret", "base_dd", "base_trades",
            "tfs_positive", "tfs_tested", "neigh_median", "neigh_share_pos", "bh"]
    tbl = show[cols].copy()
    for c in ("base_ret", "base_dd", "neigh_median", "bh"):
        tbl[c] = tbl[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    tbl["neigh_share_pos"] = tbl["neigh_share_pos"].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    print(tbl.to_string(index=False))


if __name__ == "__main__":
    main()