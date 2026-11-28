"""Finer neighbourhood + distribution checks (3-year data only)."""

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
YEAR = pd.Timestamp('2025-11-26', tz='UTC')
END = pd.Timestamp('2026-11-26', tz='UTC')


def load(coin):
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_4h.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    return d[d.ts >= CAP - pd.Timedelta(days=90)].reset_index(drop=True)


def cross(c, f, s):
    fast, slow = sma(c, f), sma(c, s)
    pf, ps = np.roll(fast, 1), np.roll(slow, 1)
    return clean((fast > slow) & (pf <= ps), s + 1), clean((fast < slow) & (pf >= ps), s + 1)


def book(raw, f, s, stop, risk=0.02, max_pos=1, score_n=30):
    data = {c: dict(o=d.open.to_numpy(float), h=d.high.to_numpy(float), l=d.low.to_numpy(float),
                    c=d.close.to_numpy(float), atr=ind.atr(d.high, d.low, d.close, 14).to_numpy(float),
                    ts=d.ts) for c, d in raw.items()}
    score = {c: np.nan_to_num(d.close.to_numpy(float) / pd.Series(d.close).shift(score_n).to_numpy(float) - 1, nan=-9.0)
             for c, d in raw.items()}
    sig = {c: cross(raw[c].close.to_numpy(float), f, s) for c in raw}
    eq, tr, expo = simulate_book(data, {c: sig[c][0] for c in raw}, {c: sig[c][1] for c in raw},
                                 score, stop_atr=stop, trail_k=None, risk=risk, max_pos=max_pos)
    return pd.Series(eq, index=pd.DatetimeIndex(next(iter(raw.values())).ts)), tr, expo


def win(s, a, b):
    w = s[(s.index >= a) & (s.index <= b)]
    return w.iloc[-1] / w.iloc[0] - 1, float((w / w.cummax() - 1).min())


def main():
    raw = {c: load(c) for c in COINS}
    print('fast/slow matrix, last-year return (stop 2.0, 1 slot, 2%):')
    print('      ' + ' '.join(f'{s:>6d}' for s in range(44, 57, 2)))
    for f in range(26, 35, 2):
        vals = []
        for s in range(44, 57, 2):
            sb, _, _ = book(raw, f, s, 2.0)
            r, _ = win(sb, YEAR, END)
            vals.append(f'{r*100:+6.1f}')
        print(f'  {f:3d} ' + ' '.join(vals))

    print('\n3-year return, same grid:')
    for f in range(26, 35, 2):
        vals = []
        for s in range(44, 57, 2):
            sb, _, _ = book(raw, f, s, 2.0)
            r, _ = win(sb, CAP, END)
            vals.append(f'{r*100:+6.0f}')
        print(f'  {f:3d} ' + ' '.join(vals))

    print('\nscore sensitivity (30/50):')
    for sn in (10, 20, 30, 60, 120):
        sb, _, _ = book(raw, 30, 50, 2.0, score_n=sn)
        ry, _ = win(sb, YEAR, END)
        r3, _ = win(sb, CAP, END)
        print(f'  score {sn:3d}: year {ry*100:+6.1f}%  3y {r3*100:+6.0f}%')

    print('\nper coin alone (3y, 1 slot):')
    for c in COINS:
        sb, trc, _ = book({c: raw[c]}, 30, 50, 2.0)
        r3, d3 = win(sb, CAP, END)
        print(f'  {c[:3]}: {r3*100:+6.0f}%  dd {d3*100:5.0f}%  n={len(trc)}')


if __name__ == "__main__":
    main()
