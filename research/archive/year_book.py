"""Book-level test: one $1,000 wallet, 1-2 slots, shared cash.

Only data from 2023-11-26 onward is used (3-year cap).
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'research'))

from bot import indicators as ind  # noqa: E402
from fastsim import simulate_book  # noqa: E402
from year_search import COINS, clean, sma  # noqa: E402

CAP_START = pd.Timestamp('2023-11-26', tz='UTC')
YEAR_START = pd.Timestamp('2025-11-26', tz='UTC')
END = pd.Timestamp('2026-11-26', tz='UTC')


def load(coin: str, tf: str = '4h') -> pd.DataFrame:
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_{tf}.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    d = d[d.ts >= CAP_START - pd.Timedelta(days=90)].reset_index(drop=True)
    return d


def cross(c, f, s):
    fast, slow = sma(c, f), sma(c, s)
    pf, ps = np.roll(fast, 1), np.roll(slow, 1)
    return clean((fast > slow) & (pf <= ps), s + 1), clean((fast < slow) & (pf >= ps), s + 1)


def stats(eq, ts, trades, exposure):
    s = pd.Series(eq, index=pd.DatetimeIndex(ts))
    out = {}
    for name, a in (('year', YEAR_START), ('3y', CAP_START)):
        w = s[s.index >= a]
        if len(w) < 10:
            continue
        ret = w.iloc[-1] / w.iloc[0] - 1
        dd = float((w / w.cummax() - 1).min())
        daily = w.resample('1D').last().dropna()
        dr = daily.pct_change().dropna()
        sharpe = float(dr.mean() / dr.std() * np.sqrt(365)) if dr.std() > 0 else 0
        out[name] = (ret, dd, sharpe)
    wins = sum(1 for r in trades if r > 0)
    out['trades'] = len(trades)
    out['win'] = wins / len(trades) if trades else 0
    out['avg_r'] = float(np.mean(trades)) if trades else 0
    out['exposure'] = exposure
    return out


def main():
    raw = {c: load(c) for c in COINS}
    data = {}
    for c, d in raw.items():
        data[c] = dict(o=d.open.to_numpy(float), h=d.high.to_numpy(float),
                       l=d.low.to_numpy(float), c=d.close.to_numpy(float),
                       atr=ind.atr(d.high, d.low, d.close, 14).to_numpy(float),
                       ts=d.ts)
    score = {c: (d.close.to_numpy(float) / pd.Series(d.close).shift(30).to_numpy(float) - 1) for c, d in raw.items()}
    for c in score:
        score[c] = np.nan_to_num(score[c], nan=-9.0)

    cfgs = []
    for f, s in [(20, 50), (25, 60), (30, 60), (30, 50), (35, 70), (40, 80)]:
        for exit_name, ex in [('trail2', dict(trail_k=2.0)), ('trail2.5', dict(trail_k=2.5)),
                              ('trail3', dict(trail_k=3.0)), ('cross', dict(trail_k=None))]:
            for stop in (1.0, 1.5, 2.0, 2.5, 3.0):
                cfgs.append((f, s, exit_name, ex, stop))
    cfgs.append(('mom90', 90, 'cross', dict(trail_k=None), 1.5))

    rows = []
    for f, s, ename, ex, stop in cfgs:
        if isinstance(f, str):
            continue
        sig = {c: cross(raw[c].close.to_numpy(float), f, s) for c in COINS}
        ent = {c: sig[c][0] for c in COINS}
        ext = {c: sig[c][1] for c in COINS}
        for max_pos, risk in itertools.product((1, 2), (0.02, 0.04)):
            eq, tr, expo = simulate_book(data, ent, ext, score, stop_atr=stop, risk=risk,
                                         max_pos=max_pos, **ex)
            st = stats(eq, raw['BTCUSDT'].ts, tr, expo)
            rows.append(dict(f=f, s=s, exit=ename, stop=stop, max_pos=max_pos, risk=risk, **st))
        print(f'  {f}/{s} {ename} stop{stop}', flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(ROOT / 'runs' / 'year_book_4h.csv', index=False)
    df['year_ret'] = df['year'].apply(lambda t: t[0])
    df['year_dd'] = df['year'].apply(lambda t: t[1])
    df['year_sh'] = df['year'].apply(lambda t: t[2])
    cols = ['f', 's', 'exit', 'stop', 'max_pos', 'risk', 'year_ret', 'year_dd', 'year_sh', 'trades', 'win', 'exposure']
    print('\nTOP 15 by last-year book return:')
    print(df.sort_values('year_ret', ascending=False).head(15)[cols].to_string(index=False))
    print('\nSame rows, 3-year window:')
    top = df.sort_values('year_ret', ascending=False).head(8)
    for _, r in top.iterrows():
        print(f"  {r.f}/{r.s} {r['exit']} stop{r.stop} pos{r.max_pos} risk{r.risk:.0%}: 3y ret {r['3y'][0]*100:+.0f}% dd {r['3y'][1]*100:.0f}% sh {r['3y'][2]:.2f}")


if __name__ == "__main__":
    main()
