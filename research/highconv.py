import sys
sys.path.insert(0, 'research')
import numpy as np, pandas as pd
import lab
from lab import SYMBOLS
pd.set_option('display.width', 200)

def panel(stop, rr, h):
    d = lab.build('1d', stop_mult=stop, rr=rr, horizon=h)
    fr = []
    for s, x in d.items():
        x = x.copy(); x['sym'] = s; fr.append(x)
    p = pd.concat(fr, ignore_index=True).dropna(subset=['y'])
    p['year'] = p.ts.dt.year
    p['trend'] = (p.px_ema50 > 0) & (p.btc_regime > 0)
    p['brk'] = p.dist_hi20 > -0.02
    return p

for stop, rr, h in ((3.0, 0.4, 20), (2.5, 0.4, 20), (2.5, 0.5, 20), (2.0, 0.5, 20)):
    p = panel(stop, rr, h)
    q = p[p.trend & p.brk]
    print(f'=== stop {stop}ATR target {rr}x stop | breakeven win {1/(1+rr):.1%} | trend+breakout n={len(q)}')
    print(f'    all: win {q.y.mean():.3f}  E[R] {q.r_mult.mean():+.3f}')
    yr = q.groupby('year').agg(win=('y','mean'), ER=('r_mult','mean'), n=('y','size'))
    print('    ' + '  '.join(f'{y}:{r.win:.2f}/{r.ER:+.2f}(n{int(r.n)})' for y, r in yr.iterrows()))
    print()
