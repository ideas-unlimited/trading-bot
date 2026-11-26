import sys
sys.path.insert(0, 'research')
import numpy as np, pandas as pd
import lab
from lab import SYMBOLS

D = {s: lab.enrich(lab.load(s, '1d')).set_index('ts') for s in SYMBOLS}
R = pd.DataFrame({s: d['close'].pct_change() for s, d in D.items()}).fillna(0.0)
IDX = R.index
MOM = pd.DataFrame({s: d['close'] / d['close'].shift(60) - 1 for s, d in D.items()})
TREND = pd.DataFrame({s: (d['close'] > d['close'].shift(60)).astype(float) for s, d in D.items()})
SMA200 = pd.DataFrame({s: (d['close'] > d['close'].rolling(200).mean()).astype(float) for s, d in D.items()})
BTC_REG = SMA200['BTCUSDT']
VOL = pd.DataFrame({s: d['close'].pct_change().rolling(30).std() * np.sqrt(365) for s, d in D.items()})

def stats(r):
    eq = (1 + r).cumprod()
    yrs = len(r) / 365.0
    vol = r.std() * np.sqrt(365)
    return {'cagr': eq.iloc[-1] ** (1/yrs) - 1, 'sharpe': r.mean()*365/vol if vol else 0,
            'dd': (eq/eq.cummax()-1).min(), 'final': eq.iloc[-1]}

def run(name, weight_fn, rebal=7):
    W = weight_fn().reindex(IDX).fillna(0.0)
    held = W.iloc[::rebal].reindex(IDX, method='ffill').shift(1).fillna(0.0)
    port = (held * R).sum(axis=1)
    st = stats(port)
    yearly = (1+port).groupby(port.index.year).prod() - 1
    print(f"{name:42s} CAGR {st['cagr']*100:6.1f}%  Sh {st['sharpe']:5.2f}  DD {st['dd']*100:6.1f}%  x{st['final']:6.2f}  expo {held.sum(axis=1).mean()*100:4.0f}%")
    print('    ' + '  '.join(f'{y}:{v*100:+.0f}%' for y, v in yearly.items()))
    return port, st

def ew_mom():
    return TREND / len(SYMBOLS)

def ew_mom_regime():
    return TREND.mul(BTC_REG, axis=0) / len(SYMBOLS)

def top2():
    rank = MOM.where(TREND > 0).rank(axis=1, ascending=False)
    w = (rank <= 2).astype(float)
    return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

def top2_regime():
    w = top2().mul(BTC_REG, axis=0)
    return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

def vol_target(w, target=0.40):
    vol = VOL.reindex(IDX).fillna(1.0)
    return (w * (target / vol).clip(upper=1.0)).clip(upper=1.0)

print('=== portfolio variants (no fees, 1k) ===')
run('mom60 in/out equal weight, daily', ew_mom, rebal=1)
run('mom60 in/out equal weight, weekly', ew_mom, rebal=7)
run('mom60 + BTC>200d, weekly', ew_mom_regime, rebal=7)
run('top-2 momentum, weekly', top2, rebal=7)
run('top-2 momentum + BTC>200d, weekly', top2_regime, rebal=7)
print()
run('mom60 + regime + 40%% vol target, weekly', lambda: vol_target(ew_mom_regime()), rebal=7)
run('top-2 + regime + 40%% vol target, weekly', lambda: vol_target(top2_regime()), rebal=7)
