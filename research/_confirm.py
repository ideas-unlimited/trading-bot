import sys; sys.path.insert(0,'src'); sys.path.insert(0,'research')
import numpy as np, pandas as pd
from bot import indicators as ind
from fastsim import simulate_book
from year_search import COINS, clean, sma
CAP = pd.Timestamp('2023-11-26', tz='UTC'); YEAR = pd.Timestamp('2025-11-26', tz='UTC'); END = pd.Timestamp('2026-11-26', tz='UTC')
raw = {}
for c in COINS:
    d = pd.read_csv(f'data/crypto/{c}_4h.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    d = d[d.ts <= END].reset_index(drop=True)
    first = d.index[d.ts >= CAP][0]
    raw[c] = d.iloc[first-70:].reset_index(drop=True)
data = {c: dict(o=d.open.to_numpy(float), h=d.high.to_numpy(float), l=d.low.to_numpy(float),
                c=d.close.to_numpy(float), atr=ind.atr(d.high,d.low,d.close,14).to_numpy(float)) for c,d in raw.items()}
score = {c: np.nan_to_num(d.close.to_numpy(float)/pd.Series(d.close).shift(30).to_numpy(float)-1, nan=-9.0) for c,d in raw.items()}
def cross(c,f,s):
    fast, slow = sma(c,f), sma(c,s); pf, ps = np.roll(fast,1), np.roll(slow,1)
    return clean((fast>slow)&(pf<=ps), s+1), clean((fast<slow)&(pf>=ps), s+1)
sig = {c: cross(raw[c].close.to_numpy(float),30,50) for c in raw}
eq, tr, expo = simulate_book(data, {c:sig[c][0] for c in raw}, {c:sig[c][1] for c in raw}, score, stop_atr=2.0, trail_k=None, risk=0.02, max_pos=1)
s = pd.Series(eq, index=pd.DatetimeIndex(raw['BTCUSDT'].ts))
for name,a in (('year',YEAR),('3y',CAP)):
    w = s[s.index>=a]; ret = w.iloc[-1]/w.iloc[0]-1; dd=(w/w.cummax()-1).min()
    dr = w.resample('1D').last().dropna().pct_change().dropna()
    print(name, f'{ret*100:+.1f}% dd {dd*100:.1f}% sharpe {dr.mean()/dr.std()*np.sqrt(365):.2f}', 'trades', len(tr))