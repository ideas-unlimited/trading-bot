"""Stage 2 of the broad sweep: are the winners rules or coin luck?

Takes finalists from `broad_stage1.csv` (top by Calmar and by return, or an
explicit `--picks PAIR|config,...`) and asks the two questions that separate a
finding from a curve fit, on the *same* one-year window:

  1. cross-chart - the same economic config on 1h/4h/6h/12h/1d. A cell that
     only exists on one chart is a coincidence of that chart.
  2. neighbours - every numeric knob x0.8 and x1.25. A plateau keeps most of
     its return; a spike collapses.

Protocol and helpers are shared with `allcoins_sweep.py` / `allcoins_deep.py`
so the numbers are comparable with `ALLCOINS.md`.

    python research/broad_deep.py [--top 6] [--picks "ZEC/USDT|lab breakout 120h s3 t5"]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_deep import TFS, cell_map, numeric_params, rebuild  # noqa: E402
from allcoins_sweep import BAR_HOURS, LOAD_DAYS, WINDOW_DAYS, run_one  # noqa: E402
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

MIN_TRADES = 8
MIN_DAYS = 330


def load_bars(pair: str, tf: str) -> pd.DataFrame | None:
    try:
        bars = load_scan_bars(pair, tf, source="binance", days=LOAD_DAYS)
    except Exception as exc:
        print(f"  skip {pair} {tf}: {exc}", flush=True)
        return None
    if len(bars) < 200:
        return None
    return bars_to_frame(bars)


def main() -> None:
    top = 6
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    d = pd.read_csv(HERE / "broad_stage1.csv")
    ok = d[(d.trades >= MIN_TRADES) & (d.window_days >= MIN_DAYS) & d.ret.notna()]
    picks: list[dict] = []

    def add(row) -> None:
        key = (row.pair, row.config)
        if key in [(p["pair"], p["config"]) for p in picks]:
            return
        picks.append(dict(pair=row.pair, config=row.config, base_tf=row.tf,
                          base_ret=float(row.ret), base_dd=float(row.dd),
                          base_calmar=float(row.calmar) if row.calmar == row.calmar else float("nan"),
                          base_trades=int(row.trades), base_is=float(row.is_ret),
                          base_oos=float(row.oos_ret), bh=float(row.bh)))

    if "--picks" in sys.argv:
        for spec in sys.argv[sys.argv.index("--picks") + 1].split(","):
            pair, label = spec.split("|", 1)
            hit = ok[(ok.pair == pair) & (ok.config == label)]
            if hit.empty:
                raise SystemExit(f"no stage-1 row for {spec}")
            add(hit.iloc[0])
    else:
        for sub in (ok.sort_values("calmar", ascending=False).head(top),
                    ok.sort_values("ret", ascending=False).head(top)):
            for _, r in sub.iterrows():
                add(r)
    print(f"{len(picks)} finalists")

    rows = []
    for p in picks:
        tf_res: dict[str, dict] = {}
        base_strat = None
        base_df = None
        for tf in TFS:
            strat = cell_map(tf).get(p["config"])
            if strat is None:
                continue
            df = load_bars(p["pair"], tf)
            if df is None:
                continue
            start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
            m = run_one(strat, df, 365.0 * 24.0 / BAR_HOURS[tf], start_ts)
            tf_res[tf] = dict(ret=m["ret"], trades=m["trades"], dd=m["dd"],
                              is_ret=m["is_ret"], oos_ret=m["oos_ret"])
            if tf == p["base_tf"]:
                base_strat, base_df = strat, df
        neigh = []
        if base_strat is not None and base_df is not None:
            start_ts = base_df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
            for name, val in list(numeric_params(base_strat).items())[:6]:
                for factor in (0.8, 1.25):
                    new = val * factor
                    if isinstance(val, int):
                        new = max(1, int(round(new)))
                    variant = rebuild(base_strat, {name: new})
                    if variant is None:
                        continue
                    try:
                        m = run_one(variant, base_df, 365.0 * 24.0 / BAR_HOURS[p["base_tf"]], start_ts)
                    except Exception:
                        continue
                    neigh.append(dict(knob=name, value=new, ret=m["ret"],
                                      trades=m["trades"]))
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
        print(f"  {p['pair']:>12} {p['config']:<26} "
              f"base {p['base_ret']:+7.1%} tf+ {p['tfs_positive']}/{p['tfs_tested']} "
              f"neigh med {p['neigh_median']:+7.1%}", flush=True)

    out = pd.DataFrame(rows)
    out["tfs"] = out["tfs"].map(json.dumps)
    name = "broad_stage2.csv"
    if "--out" in sys.argv:
        name = sys.argv[sys.argv.index("--out") + 1]
    out.to_csv(HERE / name, index=False)
    cols = ["pair", "config", "base_tf", "base_ret", "base_dd", "base_calmar",
            "base_trades", "base_is", "base_oos", "tfs_positive", "tfs_tested",
            "neigh_median", "neigh_share_pos", "bh"]
    tbl = out.sort_values(["tfs_positive", "neigh_median"], ascending=False)[cols].copy()
    for c in ("base_ret", "base_dd", "base_is", "base_oos", "neigh_median", "bh"):
        tbl[c] = tbl[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    tbl["base_calmar"] = tbl["base_calmar"].map(lambda v: "" if pd.isna(v) else f"{v:+.2f}")
    tbl["neigh_share_pos"] = tbl["neigh_share_pos"].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    print(tbl.to_string(index=False))


if __name__ == "__main__":
    main()