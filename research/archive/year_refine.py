"""Stage-two search: refine the last-year winners (entry, stop, exit)."""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'research'))

from bot import indicators as ind  # noqa: E402
from fastsim import simulate  # noqa: E402
from year_search import COINS, END, START, clean, load, metrics, sma  # noqa: E402


def build_entry(fam, p, d):
    c = d.close.to_numpy(float)
    h = d.high.to_numpy(float)
    if fam == 'cross':
        fast, slow = sma(c, p['f']), sma(c, p['s'])
        prev_f, prev_s = np.roll(fast, 1), np.roll(slow, 1)
        e = (fast > slow) & (prev_f <= prev_s)
        x = (fast < slow) & (prev_f >= prev_s)
        warm = p['s']
    elif fam == 'breakout':
        level = pd.Series(h).rolling(p['n']).max().shift(1).to_numpy()
        e = c > level
        x = c < sma(c, p['n'])
        warm = p['n']
    else:
        mom = c / pd.Series(c).shift(p['n']).to_numpy() - 1.0
        e = mom > p['thr']
        x = mom < 0
        warm = p['n']
    if p.get('regime', 0):
        e = e & (c > sma(c, p['regime']))
        warm = max(warm, p['regime'])
    return clean(e, warm + 1), clean(x, warm + 1)


def run_portfolio(data, ex, per_coin=False):
    curves, all_tr, bars, n = [], [], 0, 0
    coin_ret = {}
    for coin, (d, e, x) in data.items():
        c = d.close.to_numpy(float)
        atr = ind.atr(d.high, d.low, d.close, 14).to_numpy(float)
        eq, tr, bi = simulate(
            d.open.to_numpy(float), d.high.to_numpy(float), d.low.to_numpy(float), c,
            atr, e, x, **ex,
        )
        s = pd.Series(eq, index=pd.DatetimeIndex(d.ts))
        curves.append(s)
        all_tr += tr
        bars += bi
        n += len(d)
        coin_ret[coin] = s[s.index >= START].iloc[-1] / s[s.index >= START].iloc[0] - 1
    port = pd.concat(curves, axis=1).ffill().mean(axis=1)
    pw = port[port.index >= START]
    st = metrics(pw.to_numpy(float), pd.Series(pw.index), all_tr, bars, n)
    st['min_coin'] = min(coin_ret.values())
    if per_coin:
        st['per_coin'] = coin_ret
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='4h')
    args = ap.parse_args()
    tf = args.tf
    pre = 800 if tf == '4h' else 500
    raw = {c: load(c, tf, pre) for c in COINS}

    entries = []
    for f, s, regime in itertools.product([20, 25, 30, 35, 40], [40, 50, 60, 70, 90, 120], [0, 600, 1200]):
        if f >= s:
            continue
        entries.append(('cross', {'f': f, 's': s, 'regime': regime}))
    for n, filt, regime in itertools.product([20, 30, 55], [0, 100, 200], [0, 1200]):
        entries.append(('breakout', {'n': n, 'filt': filt, 'regime': regime}))
    for n, filt, regime in itertools.product([40, 60, 90], [0, 100], [0, 1200]):
        entries.append(('momentum', {'n': n, 'thr': 0.0, 'filt': filt, 'regime': regime}))

    # stage A: entry quality with one fixed exit
    base = dict(trail_k=None, target_r=None, stop_atr=2.5)
    rows = []
    for fam, p in entries:
        data = {c: (d, *build_entry(fam, p, d)) for c, d in raw.items()}
        st = run_portfolio(data, base)
        rows.append({'family': fam, **p, **st})
    a = pd.DataFrame(rows).sort_values('ret', ascending=False)
    print('STAGE A top 10 entries (fixed exit):')
    print(a.head(10)[['family', 'f', 's', 'n', 'filt', 'regime', 'ret', 'dd', 'sharpe', 'trades', 'min_coin']].to_string(index=False))

    top = a.head(10).to_dict('records')
    exits = [('cross_only', dict(trail_k=None, target_r=None))]
    exits += [(f'trail{k}', dict(trail_k=k, target_r=None)) for k in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)]
    exits += [(f'fix{r}R', dict(trail_k=None, target_r=r)) for r in (1.5, 2.0, 3.0)]
    exits += [('trail3.5fix2R', dict(trail_k=3.5, target_r=2.0))]
    stops = (1.5, 2.0, 2.5, 3.0, 4.0)

    rows = []
    total = len(top) * len(exits) * len(stops)
    n = 0
    for rec in top:
        fam = rec['family']
        p = {k: rec[k] for k in ('f', 's', 'n', 'filt', 'regime', 'thr') if k in rec and pd.notna(rec[k])}
        p = {k: (int(v) if k in ('f', 's', 'n', 'filt', 'regime') else float(v)) for k, v in p.items()}
        data = {c: (d, *build_entry(fam, p, d)) for c, d in raw.items()}
        for (ename, ex0), stop in itertools.product(exits, stops):
            st = run_portfolio(data, {**ex0, 'stop_atr': stop})
            rows.append({'family': fam, **p, 'exit': ename, 'stop_atr': stop, **st})
            n += 1
        print(f'  {n}/{total}', flush=True)

    df = pd.DataFrame(rows).sort_values('ret', ascending=False)
    out = ROOT / 'runs' / f'year_refine_{tf}.csv'
    df.drop(columns=['per_coin'], errors='ignore').to_csv(out, index=False)
    print(f'wrote {out} ({len(df)} configs)')
    cols = ['family', 'f', 's', 'n', 'filt', 'regime', 'exit', 'stop_atr', 'ret', 'dd', 'sharpe', 'trades', 'win', 'exposure', 'min_coin']
    print('\nTOP 15 by return:')
    print(df.head(15)[cols].to_string(index=False))
    print('\nTOP 5 by return per trade count bucket:')
    for lo in (6, 20, 50, 100):
        pool = df[df.trades >= lo]
        if len(pool):
            r = pool.iloc[0]
            print(f'  >={lo:4d} trades: {r.family} {r.exit} stop={r.stop_atr} ret={r.ret:.3f} dd={r.dd:.3f} sharpe={r.sharpe:.2f} n={r.trades:.0f}')


if __name__ == "__main__":
    main()
