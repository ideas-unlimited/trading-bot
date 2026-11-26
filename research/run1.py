
import sys, time
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab, model, sim

pd.set_option("display.width", 160)

def run(tf, stop_mult, rr, horizon, thrs=(0.50, 0.60, 0.70, 0.80)):
    t0 = time.time()
    data = lab.build(tf, stop_mult=stop_mult, rr=rr, horizon=horizon)
    p = model.panel(data, tf)
    p = model.walk_forward(p, tf, horizon=horizon)
    a = model.auc(p.y, p.p)
    base = p.y.mean()
    print(f"\n=== {tf} stop={stop_mult}ATR rr={rr} h={horizon} | OOS n={len(p)} base={base:.3f} AUC={a:.3f} ({time.time()-t0:.1f}s)")
    print(pd.DataFrame(model.reliability(p)).round(3).to_string(index=False))
    for thr in thrs:
        r = sim.simulate(p, threshold=thr, stop_mult=stop_mult, rr=rr, horizon=horizon, tf=tf)
        s = sim.stats(r, tf)
        if s:
            print(f"  thr={thr:.2f}  ret={s['return']*100:7.1f}%  cagr={s['cagr']*100:6.1f}%  sh={s['sharpe']:5.2f}  dd={s['maxdd']*100:6.1f}%  n={s['trades']:4d}  win={s.get('win_rate',0)*100:5.1f}%  avgR={s.get('avg_r',0):+.3f}  pf={s.get('pf',0):4.2f}")
    return p

p = run("1d", 2.0, 1.5, 40)
p = run("1d", 2.0, 1.0, 30)
