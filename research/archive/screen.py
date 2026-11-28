
"""Univariate screen: does any feature separate 1.5R winners from losers?"""
import sys
sys.path.insert(0, "research")
import numpy as np
import pandas as pd
import lab
from lab import FEATURES, SYMBOLS

def screen(tf="1d", stop_mult=2.0, rr=1.5, horizon=40):
    data = lab.build(tf, stop_mult=stop_mult, rr=rr, horizon=horizon)
    rows = []
    for f in FEATURES:
        for s in SYMBOLS:
            d = data[s].dropna(subset=[f, "y"])
            if len(d) < 400:
                continue
            q = pd.qcut(d[f], 5, labels=False, duplicates="drop")
            lo = d.loc[q == 0, "y"].mean()
            hi = d.loc[q == 4, "y"].mean()
            rows.append({"feat": f, "sym": s, "n": len(d), "low": lo, "high": hi, "spread": hi - lo})
    r = pd.DataFrame(rows)
    piv = r.pivot_table(index="feat", values=["low", "high", "spread"], aggfunc="mean")
    piv["spread_abs"] = piv["spread"].abs()
    return r, piv.sort_values("spread_abs", ascending=False)

if __name__ == "__main__":
    r, piv = screen()
    pd.set_option("display.width", 120)
    print(piv.round(3).to_string())
    print("\nBest single feature per symbol (top quintile win rate vs bottom):")
    for s in SYMBOLS:
        q = r[r.sym == s].sort_values("spread", ascending=False).head(3)
        print(s, [(x.feat, round(x.low, 3), round(x.high, 3)) for x in q.itertuples()])
