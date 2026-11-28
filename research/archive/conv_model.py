import sys
sys.path.insert(0, 'research')
import numpy as np, pandas as pd
import lab, model
from lab import SYMBOLS
pd.set_option('display.width', 200)

FEATS = ['px_ema50', 'dist_hi20', 'atr_pct', 'btc_regime', 'r20', 'z20', 'rsi']
d = lab.build('1d', stop_mult=3.0, rr=0.4, horizon=20)
fr = []
for s, x in d.items():
    x = x.copy(); x['sym'] = s; fr.append(x)
p = pd.concat(fr, ignore_index=True).dropna(subset=['y'])
# Only trade in the trend/breakout state: that is the strategy's universe.
p = p[(p.px_ema50 > 0) & (p.btc_regime > 0) & (p.dist_hi20 > -0.02)].copy()
print('universe n=', len(p), 'base win=', round(p.y.mean(), 3))

for feats in (FEATS, ['dist_hi20', 'atr_pct', 'btc_regime']):
    w = model.walk_forward(p.copy(), '1d', horizon=20, features=feats, min_train_days=540, block_days=90, l2=3.0)
    print(f"\nfeatures={feats} n={len(w)} AUC={model.auc(w.y, w.p):.3f}")
    print(model.reliability(w, 5).round(3).to_string(index=False))
    w['year'] = w.ts.dt.year
    hi = w[w.p >= 0.70]
    if len(hi):
        print(f'  p>=0.70: n={len(hi)} win={hi.y.mean():.3f} E[R]={hi.r_mult.mean():+.3f}')
        print('  ' + '  '.join(f'{y}:{r.y.mean():.2f}(n{int(r.y.size)})' for y, r in hi.groupby('year').y))
