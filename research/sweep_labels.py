
import sys
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab

def study(tf, stop_mult, rr, horizon):
    data = lab.build(tf, stop_mult=stop_mult, rr=rr, horizon=horizon)
    frames = []
    for s, d in data.items():
        d = d.copy(); d["sym"] = s; frames.append(d)
    p = pd.concat(frames, ignore_index=True).dropna(subset=["y"]).copy()
    p["year"] = p.ts.dt.year
    be = 1.0 / (1.0 + rr)
    return p, be

rows = []
for stop_mult in (2.0, 2.5):
    for rr in (0.75, 1.0, 1.5, 2.0):
        p, be = study("1d", stop_mult, rr, 30)
        bull = p[(p.px_ema50 > 0) & (p.btc_regime > 0)]
        dip  = bull[(bull.z20 < -0.5)]
        brk  = bull[(bull.dist_hi20 > -0.02)]
        rows.append({
            "stop": stop_mult, "rr": rr, "breakeven_win": be,
            "all_win": p.y.mean(), "all_EV": p.r_mult.mean(),
            "bull_win": bull.y.mean(), "bull_EV": bull.r_mult.mean(), "bull_n": len(bull),
            "dip_win": dip.y.mean(), "dip_EV": dip.r_mult.mean(), "dip_n": len(dip),
            "brk_win": brk.y.mean(), "brk_EV": brk.r_mult.mean(), "brk_n": len(brk),
        })
out = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(out.round(3).to_string(index=False))

# Yearly detail for the most promising configuration vs all bars
for stop_mult, rr in ((2.0, 1.0), (2.5, 1.0)):
    p, be = study("1d", stop_mult, rr, 30)
    bull = p[(p.px_ema50 > 0) & (p.btc_regime > 0)]
    print(f"\nstop={stop_mult} rr={rr} breakeven={be:.2f}  bull-regime yearly:")
    yr = bull.groupby("year").agg(win=("y","mean"), avgR=("r_mult","mean"), n=("y","size"))
    print(yr.round(3).to_string())
