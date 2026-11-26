
import sys
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab, model, sim

pd.set_option("display.width", 200)

def states(d):
    out = []
    for s, x in d.items():
        x = x.copy()
        x["sym"] = s
        x["regime"] = np.where((x.btc_regime > 0) & (x.px_ema50 > 0), "bull",
                       np.where((x.btc_regime > 0) | (x.px_ema50 > 0), "mixed", "bear"))
        x["setup"] = np.where(x.dist_hi20 > -0.02, "brk", np.where(x.z20 < -0.5, "dip", "mid"))
        x["vol"] = pd.qcut(x.atr_pct, 3, labels=["lo", "mid", "hi"], duplicates="drop").astype(str)
        out.append(x)
    return pd.concat(out, ignore_index=True)

for stop_mult, rr, horizon in ((2.5, 0.5, 20), (2.0, 1.5, 40)):
    p = states(lab.build("1d", stop_mult=stop_mult, rr=rr, horizon=horizon)).dropna(subset=["y"])
    w = model.walk_forward_bucket(p, "1d", horizon=horizon)
    print(f"\n=== bucket model | stop={stop_mult} rr={rr} h={horizon} n={len(w)} base={w.y.mean():.3f} AUC={model.auc(w.y,w.p):.3f}")
    print("calibration:"); print(model.reliability(w, 6).round(3).to_string(index=False))
    for thr in (0.60, 0.65, 0.70, 0.75):
        r = sim.simulate(w, threshold=thr, stop_mult=stop_mult, rr=rr, horizon=horizon, tf="1d")
        s = sim.stats(r, "1d")
        if s:
            print(f"  thr={thr:.2f} ret={s['return']*100:6.1f}% sh={s['sharpe']:5.2f} dd={s['maxdd']*100:6.1f}% n={s['trades']:4d} win={s.get('win_rate',0)*100:5.1f}% E[R]={s.get('avg_r',0):+.3f} pf={s.get('pf',0):.2f}")
    if len(w):
        w["year"] = w.ts.dt.year
        print("  high-conviction trades by year (p>=0.70):")
        h = w[w.p >= 0.70]
        print("  " + h.groupby("year").y.agg(["mean","size"]).round(2).to_string().replace("\n","\n  "))
