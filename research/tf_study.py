import sys
sys.path.insert(0, 'research')
import numpy as np, pandas as pd
import lab
from lab import SYMBOLS

def study(tf, lb_days, rebal_days, fee_bps=0.0, btc_gate=True):
    bars_per_day = 1 if tf == '1d' else 6
    D = {s: lab.enrich(lab.load(s, tf)).set_index('ts') for s in SYMBOLS}
    R = pd.DataFrame({s: d['close'].pct_change() for s, d in D.items()}).fillna(0.0)
    IDX = R.index
    lb = int(lb_days * bars_per_day)
    reg_n = 200 * bars_per_day
    TREND = pd.DataFrame({s: (d['close'] > d['close'].shift(lb)).astype(float) for s, d in D.items()})
    MOM = pd.DataFrame({s: d['close'] / d['close'].shift(lb) - 1 for s, d in D.items()})
    BTC_REG = (D['BTCUSDT']['close'] > D['BTCUSDT']['close'].rolling(reg_n).mean()).astype(float)
    ann = 365 * bars_per_day
    VOL = pd.DataFrame({s: d['close'].pct_change().rolling(30 * bars_per_day).std() * np.sqrt(ann) for s, d in D.items()})
    rank = MOM.where(TREND > 0).rank(axis=1, ascending=False)
    w = (rank <= 2).astype(float)
    if btc_gate: w = w.mul(BTC_REG, axis=0)
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    vol = VOL.reindex(IDX).fillna(1.0)
    w = (w * (0.40 / vol).clip(upper=1.0)).clip(upper=1.0)
    step = int(rebal_days * bars_per_day)
    held = w.iloc[::step].reindex(IDX, method='ffill').shift(1).fillna(0.0)
    gross = (held * R).sum(axis=1)
    cost = held.diff().abs().sum(axis=1).fillna(0.0) * (fee_bps / 10000.0)
    r = gross - cost
    eq = (1+r).cumprod(); yrs = len(r) / ann
    vol_ = r.std()*np.sqrt(ann)
    y = (1+r).groupby(r.index.year).prod()-1
    return {'cagr': eq.iloc[-1]**(1/yrs)-1, 'sharpe': r.mean()*ann/vol_ if vol_ else 0,
            'dd': (eq/eq.cummax()-1).min(), 'final': eq.iloc[-1], 'yearly': y}

for tf in ('1d', '4h'):
    for lb in (30, 45, 60, 90):
        s = study(tf, lb, 7, fee_bps=10)
        print(f"{tf} LB {lb:2d}d rebal weekly, 10bps: CAGR {s['cagr']*100:5.1f}%  Sh {s['sharpe']:5.2f}  DD {s['dd']*100:6.1f}%  x{s['final']:6.1f}")
        print('     ' + '  '.join(f'{y}:{v*100:+.0f}%' for y, v in s['yearly'].items()))
    print()
