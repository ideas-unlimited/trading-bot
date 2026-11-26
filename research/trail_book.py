"""Trailing exits, measured at the book level (what you would actually trade).

Entries: identical for every variant.  Exit: ratcheting stop only.  Book:
max 2 concurrent positions, 2% equity risk per trade, 50% notional cap,
no leverage, fees 10 bps per side.
"""
import sys, json, math
sys.path.insert(0, 'src'); sys.path.insert(0, 'research')
import numpy as np, pandas as pd
from lab import SYMBOLS

def atr_wilder(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    out = np.empty_like(tr); out[0] = tr[0]; a = 1.0/n
    for i in range(1, len(tr)): out[i] = out[i-1] + a*(tr[i]-out[i-1])
    return out

def load(sym):
    df = pd.read_csv(f'data/crypto/{sym}_1d.csv')
    ts = pd.to_datetime(df.timestamp, unit='s', utc=True).dt.tz_localize(None)
    h, l, c = df.high.values.astype(float), df.low.values.astype(float), df.close.values.astype(float)
    return dict(sym=sym, ts=ts.reset_index(drop=True), o=df.open.values.astype(float), h=h, l=l, c=c,
                atr=atr_wilder(h, l, c),
                sma200=pd.Series(c).rolling(200).mean().values,
                mom=pd.Series(c).pct_change(60).values,
                hi20=pd.Series(h).rolling(20).max().shift(1).values,
                sma50=pd.Series(c).rolling(50).mean().values)

def entries(d):
    n = len(d['c']); sig = np.zeros(n, dtype=bool)
    for i in range(201, n-1):
        if np.isnan(d['sma200'][i]) or np.isnan(d['atr'][i]) or np.isnan(d['hi20'][i]): continue
        if d['c'][i] > d['sma200'][i] and d['mom'][i] > 0 and d['c'][i] >= d['hi20'][i]*0.98:
            sig[i] = True
    return sig

def run(d, sig, k=3.0, mode='trail', hard_rr=None, max_hold=120, stop_atr=2.5, pct=0.0, exit_ma=None):
    n = len(d['c']); o, h, l, c, atr = d['o'], d['h'], d['l'], d['c'], d['atr']
    trades = []; i = 201
    while i < n-1:
        if not sig[i]: i += 1; continue
        e = i+1; entry = o[e]; risk = stop_atr*atr[i]
        if risk <= 0 or entry <= 0 or np.isnan(risk): i += 1; continue
        stop = entry - risk
        target = entry + hard_rr*risk if hard_rr else None
        if pct: stop = max(stop, entry*(1-pct))
        peak = max(h[e], entry); exit_px = None; exit_i = None; reason = ''
        j = e
        while j < n:
            if j > e: peak = max(peak, h[j])
            if o[j] <= stop: exit_px, exit_i, reason = o[j], j, 'gap'; break
            if l[j] <= stop: exit_px, exit_i, reason = stop, j, 'stop'; break
            if target is not None and h[j] >= target: exit_px, exit_i, reason = target, j, 'target'; break
            if j-e >= max_hold: exit_px, exit_i, reason = c[j], j, 'time'; break
            if exit_ma is not None and c[j] < d[exit_ma][j]: exit_px, exit_i, reason = c[j], j, 'ma'; break
            if mode == 'trail': stop = max(stop, peak - k*atr[j])
            elif mode == 'pct': stop = max(stop, peak*(1-k))
            j += 1
        if exit_px is None: exit_px, exit_i, reason = c[n-1], n-1, 'end'
        trades.append(dict(sym=d['sym'], entry_i=e, exit_i=exit_i, entry_ts=d['ts'].iloc[e],
                           exit_ts=d['ts'].iloc[exit_i], entry=entry, exit=exit_px,
                           qty_risk=risk, r=(exit_px-entry)/risk, mfe=(peak-entry)/risk,
                           reason=reason, bars=exit_i-e))
        i = exit_i + 1
    return trades

def book(tr, closes, fee_bps=10, risk_frac=0.02, max_pos=2, initial=1000.0):
    'Daily mark-to-market book. tr: all trades, closes: DataFrame of aligned closes.'
    dates = closes.index
    px = closes.ffill()
    for n, t in enumerate(tr):
        t['id'] = n
    by_entry, by_exit = {}, {}
    for t in tr:
        by_entry.setdefault(t['entry_ts'], []).append(t)
        by_exit.setdefault(t['exit_ts'], []).append(t)
    pos, cash, eq, expo = {}, float(initial), [], []
    for dt in dates:
        for t in by_exit.get(dt, []):
            p = pos.pop(t['id'], None)
            if p is not None:
                cash += p['qty'] * t['exit'] * (1 - fee_bps/1e4)
        mv = sum(p['qty'] * float(px.at[dt, p['sym']]) for p in pos.values())
        equity_now = cash + mv
        for t in sorted(by_entry.get(dt, []), key=lambda x: x['sym']):
            if len(pos) >= max_pos or any(p['sym'] == t['sym'] for p in pos.values()):
                continue
            notional = min(risk_frac * equity_now * t['entry'] / t['qty_risk'], 0.5 * equity_now, cash)
            if notional < 10:
                continue
            qty = notional / t['entry']
            cash -= notional * (1 + fee_bps / 1e4)
            pos[t['id']] = dict(sym=t['sym'], qty=qty, entry=t['entry'])
        mv = sum(p['qty'] * float(px.at[dt, p['sym']]) for p in pos.values())
        equity = cash + mv
        eq.append((dt, equity))
        expo.append(mv / equity if equity > 0 else 0.0)
    return pd.Series(dict(eq)), pd.Series(expo)

def stats(s, expo, tf=365.0):
    r = s.pct_change().dropna()
    yrs = len(r)/tf
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1
    vol = r.std()*math.sqrt(tf)
    dd = (s/s.cummax()-1).min()
    return dict(cagr=cagr, vol=vol, sharpe=r.mean()*tf/vol if vol else np.nan, dd=dd,
                final=s.iloc[-1], calmar=cagr/abs(dd) if dd<0 else np.nan, expo=expo.mean())

data = {s: load(s) for s in SYMBOLS}
sig = {s: entries(data[s]) for s in SYMBOLS}
closes = pd.DataFrame({s: pd.Series(data[s]['c'], index=data[s]['ts']) for s in SYMBOLS}).sort_index()
closes = closes[~closes.index.duplicated(keep='last')]

configs = []
for k in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0):
    configs.append((f'trail {k}ATR', dict(k=k, mode='trail')))
for p in (0.15, 0.20, 0.25, 0.30):
    configs.append((f'trail {int(p*100)}%', dict(k=p, mode='pct')))
for rr in (1.2, 2.0, 3.0, 4.0):
    configs.append((f'hard {rr}R', dict(hard_rr=rr, mode='fixed')))
for k in (3.0, 4.0):
    configs.append((f'trail {k}ATR + MA50 exit', dict(k=k, mode='trail', exit_ma='sma50')))

rows = []
per_year = {}
for name, cfg in configs:
    tr = []
    for s in SYMBOLS:
        tr += run(data[s], sig[s], **cfg)
    eq, expo = book(tr, closes)
    st = stats(eq, expo)
    tdf = pd.DataFrame(tr)
    st['name'] = name; st['trades'] = len(tdf); st['win'] = float((tdf.r > 0).mean());
    st['ev'] = float(tdf.r.mean()); st['median_r'] = float(tdf.r.median())
    cap = tdf[tdf.mfe >= 1].assign(c=lambda x: (x.r/x.mfe).clip(0, 1.2))
    st['capture'] = float(cap.c.median()); st['giveback'] = float((tdf.mfe-tdf.r).median())
    st['bars'] = float(tdf.bars.median())
    rows.append(st)
    per_year[name] = (1+eq.pct_change().dropna()).groupby(eq.pct_change().dropna().index.year).prod()-1

tab = pd.DataFrame(rows)[['name','trades','win','ev','median_r','capture','giveback','bars','cagr','vol','sharpe','dd','calmar','expo','final']]
pd.set_option('display.width', 230)
print(tab.round(3).to_string(index=False))
tab.to_csv('runs/trailing_book.csv', index=False)
py = pd.DataFrame(per_year)
print(); print('yearly returns:'); print((py*100).round(0).to_string())
py.to_csv('runs/trailing_years.csv')
