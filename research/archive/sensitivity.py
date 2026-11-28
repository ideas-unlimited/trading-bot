"""Sensitivity grid + monthly stats for the leading geometry (3y window)."""
import sys
sys.path.insert(0, "research")

import numpy as np
import pandas as pd

import lab, sim
from validate_geom import gate_frame, CUTOFF_YEARS


def monthly(res):
    eq = res["equity"].copy()
    m = eq.resample("ME").last()
    ret = m.pct_change().dropna()
    return ret


if __name__ == "__main__":
    for tf, grid in (
        ("4h", [dict(stop=s, rr=r, horizon=h)
                for s in (1.5, 2.0, 2.5, 3.0)
                for r in (1.0, 1.5, 2.0)
                for h in (30, 40, 50)]),
        ("1d", [dict(stop=s, rr=r, horizon=h)
                for s in (1.5, 2.0, 2.5, 3.0)
                for r in (1.0, 1.5, 2.0)
                for h in (20, 30, 40)]),
    ):
        rows = []
        for cfg in grid:
            p = gate_frame(tf, cfg["stop"], cfg["rr"], cfg["horizon"], "brk", "bull")
            r = sim.simulate(p, threshold=0.5, stop_mult=cfg["stop"], rr=cfg["rr"],
                             horizon=cfg["horizon"], risk=0.02, max_pos=2, tf=tf, fee_bps=0.0)
            s = sim.stats(r, tf)
            if not s:
                continue
            m = monthly(r)
            s.update(stop=cfg["stop"], rr=cfg["rr"], h=cfg["horizon"],
                     month_win=float((m > 0).mean()), n_months=len(m))
            rows.append(s)
        df = pd.DataFrame(rows).sort_values("return", ascending=False)
        cols = ["stop", "rr", "h", "return", "cagr", "sharpe", "maxdd", "trades",
                "win_rate", "avg_r", "pf", "month_win", "n_months"]
        print(f"\n=== {tf} sensitivity (risk 2%, max_pos 2, 0 fees) ===")
        print(df[cols].round(3).to_string(index=False))
