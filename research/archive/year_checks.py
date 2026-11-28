"""Robustness checks for the chosen book: yearly buckets, per-coin, costs."""

from __future__ import annotations

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

CAP = pd.Timestamp('2023-11-26', tz='UTC')
END = pd.Timestamp('2026-11-26', tz='UTC')


def load(coin):
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_4h.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    return d[d.ts >= CAP - pd.Timedelta(days=90)].reset_index(drop=True)


def cross(c, f, s):
    fast, slow = sma(c, f), sma(c, s)
    pf, ps = np.roll(fast, 1), np.roll(slow, 1)
    return clean((fast > slow) & (pf <= ps), s + 1), clean((fast < slow) & (pf >= ps), s + 1)


def book(raw, f, s, stop, risk=0.02, fee=0.001, slip=0.0005, max_pos=1):
    data = {c: dict(o=d.open.to_numpy(float), h=d.high.to_numpy(float), l=d.low.to_numpy(float),
                    c=d.close.to_numpy(float), atr=ind.atr(d.high, d.low, d.close, 14).to_numpy(float),
                    ts=d.ts) for c, d in raw.items()}
    score = {c: np.nan_to_num(d.close.to_numpy(float) / pd.Series(d.close).shift(30).to_numpy(float) - 1, nan=-9.0)
             for c, d in raw.items()}
    sig = {c: cross(raw[c].close.to_numpy(float), f, s) for c in raw}
    ent = {c: sig[c][0] for c in raw}
    ext = {c: sig[c][1] for c in raw}
    eq, tr, expo = simulate_book(data, ent, ext, score, stop_atr=stop, trail_k=None,
                                 risk=risk, fee=fee, slip=slip, max_pos=max_pos)
    return pd.Series(eq, index=pd.DatetimeIndex(raw['BTCUSDT'].ts)), tr, expo


def window(s, a, b):
    w = s[(s.index >= a) & (s.index <= b)]
    ret = w.iloc[-1] / w.iloc[0] - 1
    dd = float((w / w.cummax() - 1).min())
    dr = w.resample('1D').last().dropna().pct_change().dropna()
    sh = float(dr.mean() / dr.std() * np.sqrt(365)) if dr.std() > 0 else 0
    return ret, dd, sh


def main():
    raw = {c: load(c) for c in COINS}
    s, tr, expo = book(raw, 30, 50, 2.0)
    print(f'book: {len(tr)} trades, win {np.mean([t>0 for t in tr])*100:.0f}%, avg {np.mean(tr):+.2f}R, exposure {expo*100:.0f}%')
    buckets = [
        ('2023-11 -> 2024-11', pd.Timestamp('2023-11-26', tz='UTC'), pd.Timestamp('2024-11-26', tz='UTC')),
        ('2024-11 -> 2025-11', pd.Timestamp('2024-11-26', tz='UTC'), pd.Timestamp('2025-11-26', tz='UTC')),
        ('2025-11 -> 2026-11', pd.Timestamp('2025-11-26', tz='UTC'), pd.Timestamp('2026-11-26', tz='UTC')),
    ]
    for name, a, b in buckets:
        r, d, sh = window(s, a, b)
        print(f'  {name}: {r*100:+6.1f}%  dd {d*100:5.1f}%  sharpe {sh:.2f}')
    r, d, sh = window(s, CAP, END)
    print(f'  full 3y: {r*100:+.0f}%  dd {d*100:.0f}%  sharpe {sh:.2f}  CAGR {((1+r)**(1/3)-1)*100:.0f}%')

    print('\nneighbourhood (last 12m / 3y):')
    for f, sl, st in [(28, 45, 2.0), (30, 45, 2.0), (30, 50, 1.75), (30, 50, 2.0), (30, 50, 2.25),
                      (30, 55, 2.0), (32, 50, 2.0), (30, 50, 2.5)]:
        sb, _, _ = book(raw, f, sl, st)
        ry, dy, shy = window(sb, pd.Timestamp('2025-11-26', tz='UTC'), END)
        r3, d3, sh3 = window(sb, CAP, END)
        print(f'  {f}/{sl} stop{st}: year {ry*100:+6.1f}% (dd {dy*100:5.1f}%)  3y {r3*100:+6.0f}% (dd {d3*100:4.0f}%) sh {sh3:.2f}')

    print('\ncosts (last 12m):')
    for bps in (10, 20, 30):
        sb, _, _ = book(raw, 30, 50, 2.0, fee=bps / 10_000)
        ry, dy, _ = window(sb, pd.Timestamp('2025-11-26', tz='UTC'), END)
        print(f'  {bps} bps: {ry*100:+.1f}%  dd {dy*100:.1f}%')

    print('\nper coin alone (3y):')
    for c in COINS:
        sb, trc, _ = book({c: raw[c]}, 30, 50, 2.0)
        r3, d3, sh3 = window(sb, CAP, END)
        print(f'  {c[:3]}: {r3*100:+6.0f}%  dd {d3*100:5.0f}%  sh {sh3:.2f}  n={len(trc)}')

    # monthly returns for the chart
    m = s.resample('1ME').last().pct_change().dropna()
    m.to_csv(ROOT / 'runs' / 'year_book_monthly.csv')
    print('\nworst months:', ', '.join(f'{i:%Y-%m} {v*100:+.1f}%' for i, v in m.nsmallest(4).items()))


if __name__ == "__main__":
    main()
