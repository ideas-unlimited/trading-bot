
import sys
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab, model

data = lab.build("1d", stop_mult=2.0, rr=1.5, horizon=40)
p = model.panel(data, "1d").dropna(subset=["y"]).copy()
p["year"] = p.ts.dt.year

print("Unconditional 1.5R win rate by year:")
print(p.groupby("year").y.agg(["mean", "size"]).round(3).to_string())

# Stable, interpretable states
p["up_trend"] = (p.px_ema50 > 0)
p["btc_up"] = (p.btc_regime > 0)
p["mom_top"] = p.groupby("ts").r20.transform(lambda s: s.rank(pct=True)) > 0.5

for name, mask in [
    ("all", np.ones(len(p), bool)),
    ("coin>ema50", p.up_trend.values),
    ("btc>sma200", p.btc_up.values),
    ("both", (p.up_trend & p.btc_up).values),
    ("both & top-half 20d mom", (p.up_trend & p.btc_up & p.mom_top).values),
    ("low vol (atr% bot half)", (p.atr_pct < p.atr_pct.median()).values),
]:
    q = p[mask]
    print(f"\n{name:28s} n={len(q):5d} win={q.y.mean():.3f}  avgR={q.r_mult.mean():+.3f}")
    yr = q.groupby("year").agg(win=("y","mean"), avgR=("r_mult","mean"), n=("y","size"))
    print("   " + "  ".join(f"{y}:{r.win:.2f}/{r.avgR:+.2f}" for y, r in yr.iterrows() if r.n > 30))
