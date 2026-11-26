
import sys
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab, model

# Geometry scan: where is 70% win rate physically reachable?
rows = []
for stop_mult in (2.0, 2.5, 3.0):
    for rr in (0.4, 0.5, 0.6, 0.75):
        d = lab.build("1d", stop_mult=stop_mult, rr=rr, horizon=20)
        fr = []
        for s, x in d.items():
            x = x.copy(); x["sym"] = s; fr.append(x)
        p = pd.concat(fr, ignore_index=True).dropna(subset=["y"])
        bull = p[(p.px_ema50 > 0) & (p.btc_regime > 0)]
        brk = bull[bull.dist_hi20 > -0.02]
        rows.append({"stop": stop_mult, "rr": rr, "be": 1/(1+rr),
                     "all_win": p.y.mean(), "bull_win": bull.y.mean(),
                     "brk_win": brk.y.mean(), "brk_n": len(brk),
                     "brk_EV": brk.r_mult.mean(), "brk_win_lo": brk.y.mean() - 1.96*np.sqrt(brk.y.mean()*(1-brk.y.mean())/max(len(brk),1))})
out = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(out.round(3).to_string(index=False))
