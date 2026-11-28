"""Validate candidate rule-based geometries on the 3-year window, year by year.

Reuses the peer's sim engine.  A deterministic rule gate is expressed as
p=1.0 (qualifies) / 0.0 (not), so sim.simulate can be driven unchanged.
No fee assumption (user asked for 0 fees).
"""
import sys
sys.path.insert(0, "research")

import numpy as np
import pandas as pd

import lab, sim

CUTOFF_YEARS = 3


def gate_frame(tf, stop_mult, rr, horizon, setup="brk", regime="bull"):
    data = lab.build(tf, stop_mult=stop_mult, rr=rr, horizon=horizon)
    cutoff = max(d["ts"].max() for d in data.values()) - pd.DateOffset(years=CUTOFF_YEARS)
    fr = []
    for s, x in data.items():
        x = x[x.ts >= cutoff].copy()
        x["sym"] = s
        bull = (x.px_ema50 > 0) & (x.btc_regime > 0)
        anym = (x.px_ema50 > 0) | (x.btc_regime > 0)
        r = bull if regime == "bull" else anym
        if setup == "brk":
            q = r & (x.dist_hi20 > -0.02)
        elif setup == "dip":
            q = r & (x.z20 < -0.5)
        elif setup == "both":
            q = r & ((x.dist_hi20 > -0.02) | (x.z20 < -0.5))
        else:
            q = r
        x["p"] = np.where(q, 1.0, 0.0)
        x["atr"] = x["atr"]
        fr.append(x[["ts", "sym", "open", "high", "low", "close", "atr", "p", "y", "r_mult"]])
    return pd.concat(fr, ignore_index=True).sort_values(["ts", "sym"]).reset_index(drop=True)


def run(tf, configs):
    out = []
    for cfg in configs:
        p = gate_frame(tf, cfg["stop"], cfg["rr"], cfg["horizon"], cfg.get("setup", "brk"),
                       cfg.get("regime", "bull"))
        base = p.y.mean()
        for thr, risk, mx in ((0.5, cfg.get("risk", 0.02), cfg.get("max_pos", 2)),):
            r = sim.simulate(p, threshold=thr, stop_mult=cfg["stop"], rr=cfg["rr"],
                             horizon=cfg["horizon"], risk=risk, max_pos=mx, tf=tf, fee_bps=0.0)
            s = sim.stats(r, tf)
            if not s:
                continue
            tr = r["trades"].copy()
            tr["year"] = pd.to_datetime(tr["open_ts"]).dt.year
            yr = tr.groupby("year").agg(n=("r", "size"), win=("r", lambda x: (x > 0).mean()),
                                        ev=("r", "mean"))
            print(f"\n[{tf}] stop={cfg['stop']} rr={cfg['rr']} h={cfg['horizon']} "
                  f"setup={cfg.get('setup','brk')} risk={risk} mx={mx}  label-base={base:.3f}")
            print(f"  TOTAL ret={s['return']*100:7.1f}% cagr={s['cagr']*100:5.1f}% "
                  f"sh={s['sharpe']:5.2f} dd={s['maxdd']*100:6.1f}% n={s['trades']:4d} "
                  f"win={s['win_rate']*100:5.1f}% avgR={s['avg_r']:+.3f} pf={s['pf']:.2f} "
                  f"bars/trade={s.get('avg_bars',0):.0f}")
            print("  " + " | ".join(f"{int(y)}: n={int(v.n)} win={v.win*100:.0f}% EV={v.ev:+.2f}"
                                    for y, v in yr.iterrows()))
            out.append({**cfg, **s})
    return pd.DataFrame(out)


if __name__ == "__main__":
    print("============ 1d ============")
    run("1d", [
        dict(stop=2.0, rr=1.5, horizon=40, setup="brk", risk=0.02),
        dict(stop=2.5, rr=1.5, horizon=40, setup="brk", risk=0.02),
        dict(stop=1.5, rr=1.0, horizon=30, setup="brk", risk=0.02),
        dict(stop=1.5, rr=0.5, horizon=20, setup="brk", risk=0.02),
        dict(stop=2.0, rr=1.5, horizon=40, setup="dip", risk=0.02),
        dict(stop=2.0, rr=1.5, horizon=40, setup="both", risk=0.02),
        dict(stop=2.0, rr=1.5, horizon=40, setup="both", regime="any", risk=0.02),
    ])
    print("\n============ 4h ============")
    run("4h", [
        dict(stop=2.0, rr=1.5, horizon=40, setup="brk", risk=0.02),
        dict(stop=1.5, rr=1.0, horizon=30, setup="brk", risk=0.02),
        dict(stop=1.5, rr=0.5, horizon=20, setup="brk", risk=0.02),
        dict(stop=2.0, rr=1.5, horizon=40, setup="dip", risk=0.02),
    ])
