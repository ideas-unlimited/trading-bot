import sys, json, math
sys.path.insert(0, 'src'); sys.path.insert(0, 'research')
import numpy as np, pandas as pd
from lab import SYMBOLS
src = open('research/trail_book.py').read().split('data = {s: load(s)')[0]
ns = {}; exec(compile(src, 'trail_book.py', 'exec'), ns)
load, entries, run, book, stats = ns['load'], ns['entries'], ns['run'], ns['book'], ns['stats']
from bot.portfolio import RotationConfig, simulate_rotation, load_closes_from_csv

data = {s: load(s) for s in SYMBOLS}
sig = {s: entries(data[s]) for s in SYMBOLS}
closes = pd.DataFrame({s: pd.Series(data[s]['c'], index=data[s]['ts']) for s in SYMBOLS}).sort_index()
closes = closes[~closes.index.duplicated(keep='last')]
tr = []
for s in SYMBOLS: tr += run(data[s], sig[s], k=3.5, mode='trail')
swing_eq, swing_expo = book(tr, closes, risk_frac=0.03)
swing_eq = swing_eq[swing_eq.index >= '2019-01-01']

rot = simulate_rotation(closes, RotationConfig(top_n=2, vol_target=0.40, fee_bps=10, initial_cash=1000.0))
rot_eq = rot.equity.reindex(swing_eq.index).ffill()

print('correlation of daily returns:', round(float(rot_eq.pct_change().corr(swing_eq.pct_change())), 3))
print()
print(f"{'mix':>8} {'cagr':>7} {'vol':>6} {'sharpe':>7} {'dd':>7} {'final':>9}")
for w in (0.0, 0.3, 0.5, 0.7, 1.0):
    eq = w*rot_eq/rot_eq.iloc[0] + (1-w)*swing_eq/swing_eq.iloc[0]
    st = stats(eq, pd.Series(0.0, index=eq.index))
    print(f"{int(w*100):>3}/{int((1-w)*100):<3} {st['cagr']*100:>6.1f}% {st['vol']*100:>5.1f}% {st['sharpe']:>7.2f} {st['dd']*100:>6.1f}% {st['final']:>9.0f}")

# yearly for the 70/30 and 100/0
for name, eq in (('rotation', rot_eq), ('swing 3%', swing_eq), ('70/30', 0.7*rot_eq/rot_eq.iloc[0]+0.3*swing_eq/swing_eq.iloc[0])):
    y = (1+eq.pct_change().dropna()).groupby(eq.pct_change().dropna().index.year).prod()-1
    print(f"{name:<9}", ' '.join(f"{yr}:{v*100:+.0f}%" for yr, v in y.items()))

# save for the report
out = json.load(open('runs/report_data.json'))
out['trail'] = {'params': {'trail_atr': 3.5, 'stop_atr': 2.5, 'risk': 0.03}, 'summary': stats(swing_eq, swing_expo),
                'yearly': {str(k): float(v) for k, v in (1+swing_eq.pct_change().dropna()).groupby(swing_eq.pct_change().dropna().index.year).prod().sub(1).items()}}
out['trail']['curve'] = {'ts': [d.strftime('%Y-%m-%d') for d in swing_eq.index], 'eq': [float(x) for x in swing_eq.values]}
out['combo'] = {}
for w in (0.0, 0.3, 0.5, 0.7, 1.0):
    eq = w*rot_eq/rot_eq.iloc[0] + (1-w)*swing_eq/swing_eq.iloc[0]
    st = stats(eq, pd.Series(0.0, index=eq.index))
    out['combo'][str(w)] = {k: float(v) for k, v in st.items()}
out['corr'] = float(rot_eq.pct_change().corr(swing_eq.pct_change()))
json.dump(out, open('runs/report_data.json','w'), indent=1)
print(); print('saved')
