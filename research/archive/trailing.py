"""Trailing-exit study.

Entry: coin > 200d SMA, 60-bar momentum > 0, close within 2% of the prior
20-bar high.  Fill next open.  Exit policies are compared on identical
entries, so any difference is the exit alone.
"""
import sys, json, math
sys.path.insert(0, 'src'); sys.path.insert(0, 'research')
import numpy as np, pandas as pd
from lab import SYMBOLS

def atr_wilder(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    out = np.empty_like(tr); out[0] = tr[0]
    a = 1.0 / n
    for i in range(1, len(tr)):
        out[i] = out[i-1] + a * (tr[i] - out[i-1])
    return out

def load(sym):
    df = pd.read_csv(f'data/crypto/{sym}_1d.csv')
    df['ts'] = pd.to_datetime(df.timestamp, unit='s', utc=True).dt.tz_localize(None)
    h, l, c = df.high.values.astype(float), df.low.values.astype(float), df.close.values.astype(float)
    atr = atr_wilder(h, l, c)
    sma200 = pd.Series(c).rolling(200).mean().values
    mom = pd.Series(c).pct_change(60).values
    hi20 = pd.Series(h).rolling(20).max().shift(1).values
    return dict(ts=df.ts.values, o=df.open.values.astype(float), h=h, l=l, c=c,
                atr=atr, sma200=sma200, mom=mom, hi20=hi20)

def entries(d, stop_atr=2.5):
    n = len(d['c']); sig = np.zeros(n, dtype=bool)
    for i in range(201, n-1):
        if np.isnan(d['sma200'][i]) or np.isnan(d['atr'][i]) or np.isnan(d['hi20'][i]): continue
        if d['c'][i] > d['sma200'][i] and d['mom'][i] > 0 and d['c'][i] >= d['hi20'][i] * 0.98:
            sig[i] = True
    return sig

def run(d, sig, mode='trail', k=3.0, hard_rr=None, be_at=None, max_hold=250, stop_atr=2.5):
    n = len(d['c']); o, h, l, c, atr = d['o'], d['h'], d['l'], d['c'], d['atr']
    trades = []
    i = 201
    while i < n - 1:
        if not sig[i]: i += 1; continue
        e = i + 1
        entry = o[e]
        risk = stop_atr * atr[i]
        if risk <= 0 or entry <= 0: i += 1; continue
        stop = entry - risk
        target = entry + hard_rr * risk if hard_rr else None
        peak = max(h[e], entry)
        exit_px = None; exit_i = None; reason = ''
        j = e
        while j < n:
            if j > e:
                peak = max(peak, h[j])
            # gap through stop
            if o[j] <= stop:
                exit_px, exit_i, reason = o[j], j, 'gap'; break
            if l[j] <= stop:
                exit_px, exit_i, reason = stop, j, 'stop'; break
            if target is not None and h[j] >= target:
                exit_px, exit_i, reason = target, j, 'target'; break
            if j - e >= max_hold:
                exit_px, exit_i, reason = c[j], j, 'time'; break
            # ratchet AFTER the bar's exit check: new stop binds from next bar
            if mode == 'trail':
                stop = max(stop, peak - k * atr[j])
            elif mode == 'pct':
                stop = max(stop, peak * (1 - k))
            if be_at is not None and peak >= entry + be_at * risk:
                stop = max(stop, entry)
            j += 1
        if exit_px is None:
            exit_px, exit_i, reason = c[n-1], n-1, 'end'
        mfe = (peak - entry) / risk
        r = (exit_px - entry) / risk
        trades.append(dict(entry_i=e, exit_i=exit_i, entry_ts=d['ts'][e], exit_ts=d['ts'][exit_i],
                           r=r, mfe=mfe, reason=reason, bars=exit_i - e))
        i = exit_i + 1
    return pd.DataFrame(trades)

def stats(t):
    if not len(t): return {}
    win = t.r > 0
    return dict(n=len(t), win=float(win.mean()), avg_win=float(t.r[win].mean()) if win.any() else 0,
                avg_loss=float(t.r[~win].mean()) if (~win).any() else 0, ev=float(t.r.mean()),
                med=float(t.r.median()), capture=float((t.r / t.mfe.replace(0, np.nan)).clip(0, 1.5).median()),
                mfe=float(t.mfe.median()), giveback=float((t.mfe - t.r).median()), bars=float(t.bars.median()))

data = {s: load(s) for s in SYMBOLS}
sig = {s: entries(data[s]) for s in SYMBOLS}
print('entries per coin:', {s.replace('USDT',''): int(sig[s].sum()) for s in SYMBOLS})

rows = []
for k in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0):
    allt = pd.concat([run(data[s], sig[s], 'trail', k=k).assign(sym=s) for s in SYMBOLS])
    st = stats(allt); st['k'] = k; st['kind'] = 'atr trail'
    rows.append(st)
for p in (0.10, 0.15, 0.20, 0.25, 0.30):
    allt = pd.concat([run(data[s], sig[s], 'pct', k=p).assign(sym=s) for s in SYMBOLS])
    st = stats(allt); st['k'] = p; st['kind'] = 'percent trail'
    rows.append(st)
for rr in (1.2, 2.0, 3.0, 4.0, 6.0):
    allt = pd.concat([run(data[s], sig[s], 'fixed', hard_rr=rr).assign(sym=s) for s in SYMBOLS])
    st = stats(allt); st['k'] = rr; st['kind'] = f'hard {rr}R target'
    rows.append(st)
for k in (2.0, 2.5, 3.0, 4.0):
    allt = pd.concat([run(data[s], sig[s], 'trail', k=k, be_at=1.0).assign(sym=s) for s in SYMBOLS])
    st = stats(allt); st['k'] = k; st['kind'] = 'trail after +1R'
    rows.append(st)

tab = pd.DataFrame(rows)[['kind','k','n','win','avg_win','avg_loss','ev','capture','mfe','giveback','bars']]
pd.set_option('display.width', 200)
print(tab.round(3).to_string(index=False))
tab.to_csv('runs/trailing_sweep.csv', index=False)
