"""Refine the trailing result: robustness, stop width, risk scaling, give-back."""
import sys, json, math
sys.path.insert(0, 'src'); sys.path.insert(0, 'research')
import numpy as np, pandas as pd
from lab import SYMBOLS

# reuse the simulator without re-running its sweep
src = open('research/trail_book.py').read().split('data = {s: load(s)')[0]
ns = {}; exec(compile(src, 'trail_book.py', 'exec'), ns)
load, entries, run, book, stats = ns['load'], ns['entries'], ns['run'], ns['book'], ns['stats']

data = {s: load(s) for s in SYMBOLS}
sig = {s: entries(data[s]) for s in SYMBOLS}
closes = pd.DataFrame({s: pd.Series(data[s]['c'], index=data[s]['ts']) for s in SYMBOLS}).sort_index()
closes = closes[~closes.index.duplicated(keep='last')]

def build(cfg, coins=None):
    coins = coins or SYMBOLS
    tr = []
    for s in coins: tr += run(data[s], sig[s], **cfg)
    eq, expo = book(tr, closes)
    st = stats(eq, expo); t = pd.DataFrame(tr)
    st.update(trades=len(t), win=float((t.r > 0).mean()), ev=float(t.r.mean()))
    return st, t, eq

print('=== per coin, trail 3.5 ATR ===')
for s in SYMBOLS:
    st, t, eq = build(dict(k=3.5, mode='trail'), [s])
    print(f"{s.replace('USDT',''):<5} n={len(t):>3} win={st['win']*100:>4.0f}% ev={st['ev']:+.2f}R "
          f"cagr={st['cagr']*100:>5.1f}% dd={st['dd']*100:>6.1f}% sharpe={st['sharpe']:.2f}")

print()
print('=== stop width (trail 3.5 ATR) ===')
for sa in (2.0, 2.5, 3.0, 3.5):
    st, t, eq = build(dict(k=3.5, mode='trail', stop_atr=sa))
    print(f"initial {sa} ATR: n={len(t):>3} ev={st['ev']:+.2f}R cagr={st['cagr']*100:>5.1f}% dd={st['dd']*100:>6.1f}% sharpe={st['sharpe']:.2f}")

print()
print('=== extra exits on top of trail 3.5 ATR ===')
for name, extra in (('none', {}), ('momentum<0', dict(exit_ma=None)), ('close<sma200', dict(exit_ma='sma200'))):
    st, t, eq = build(dict(k=3.5, mode='trail', **extra))
    print(f"{name:<14} n={len(t):>3} ev={st['ev']:+.2f}R cagr={st['cagr']*100:>5.1f}% dd={st['dd']*100:>6.1f}% sh={st['sharpe']:.2f}")

print()
print('=== risk per trade (trail 3.5 ATR) ===')
for rf in (0.01, 0.02, 0.03, 0.04):
    tr = []
    for s in SYMBOLS: tr += run(data[s], sig[s], k=3.5, mode='trail')
    eq, expo = book(tr, closes, risk_frac=rf)
    st = stats(eq, expo)
    print(f"risk {rf*100:.0f}%: cagr={st['cagr']*100:>5.1f}% vol={st['vol']*100:>4.1f}% dd={st['dd']*100:>6.1f}% sharpe={st['sharpe']:.2f} final={st['final']:>8.0f}")

# give-back decomposition, winners only
tr = []
for s in SYMBOLS: tr += run(data[s], sig[s], k=3.5, mode='trail')
t = pd.DataFrame(tr); w = t[t.r > 0]
print()
print('=== give-back, trail 3.5 ATR ===')
print(f'trades {len(t)}, winners {len(w)} ({len(w)/len(t)*100:.0f}%)')
print(f'winners: median peak {w.mfe.median():.2f}R, median realised {w.r.median():.2f}R, median capture {100*(w.r/w.mfe).clip(0,1).median():.0f}%')
print(f'all: median capture {100*(t.r/t.mfe.replace(0,np.nan)).clip(0,1.2).median():.0f}%')
print(f'avg bars held {t.bars.mean():.0f}, median {t.bars.median():.0f}, max {t.bars.max()}')
print(f'reasons: {t.reason.value_counts().to_dict()}')
print(f'top 5 trades share of total R: {100*t.nlargest(5,"r").r.sum()/t.r.sum():.0f}%')
print(f'top 10 trades share of total R: {100*t.nlargest(10,"r").r.sum()/t.r.sum():.0f}%')
# what a perfect exit would have paid
print(f'sum R actual {t.r.sum():.1f} vs perfect {t.mfe.sum():.1f} -> capture {100*t.r.sum()/t.mfe.sum():.0f}%')
t.to_csv('runs/trailing_trades_35.csv', index=False)
json.dump(dict(sym=list(SYMBOLS)), open('runs/_tmp.json','w'))
