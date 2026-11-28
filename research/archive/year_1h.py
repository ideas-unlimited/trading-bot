"""Does 1h beat 4h?  Same cross family, 3-year data only."""

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
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_1h.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    return d[d.ts >= CAP - pd.Timedelta(days=60)].reset_index(drop=True)


def cross(c, f, s):
    fast, slow = sma(c, f), sma(c, s)
    pf, ps = np.roll(fast, 1), np.roll(slow, 1)
    return clean((fast > slow) & (pf <= ps), s + 1), clean((fast < slow) & (pf >= ps), s + 1)


def win(s, a, b):
    w = s[(s.index >= a) & (s.index <= b)]
    dr = w.resample('1D').last().dropna().pct_change().dropna()
    sh = float(dr.mean() / dr.std() * np.sqrt(365)) if dr.std() > 0 else 0
    return w.iloc[-1] / w.iloc[0] - 1, float((w / w.cummax() - 1).min()), sh


def main():
    raw = {c: load(c) for c in COINS}
    data = {c: dict(o=d.open.to_numpy(float), h=d.high.to_numpy(float), l=d.low.to_numpy(float),
                    c=d.close.to_numpy(float), atr=ind.atr(d.high, d.low, d.close, 14).to_numpy(float))
            for c, d in raw.items()}
    score = {c: np.nan_to_num(d.close.to_numpy(float) / pd.Series(d.close).shift(60).to_numpy(float) - 1, nan=-9.0)
             for c, d in raw.items()}
    for f, s in [(60, 120), (90, 180), (120, 240), (180, 300), (120, 300), (180, 360), (240, 480), (90, 300)]:
        sig = {c: cross(raw[c].close.to_numpy(float), f, s) for c in raw}
        eq, tr, _ = simulate_book(data, {c: sig[c][0] for c in raw}, {c: sig[c][1] for c in raw},
                                  score, stop_atr=2.0, trail_k=None, risk=0.02, max_pos=1)
        s_eq = pd.Series(eq, index=pd.DatetimeIndex(raw['BTCUSDT'].ts))
        ry, dy, shy = win(s_eq, YEAR, END)
        r3, d3, sh3 = win(s_eq, CAP, END)
        print(f'  {f:3d}/{s:3d}: year {ry*100:+7.1f}% (dd {dy*100:5.1f}%)  3y {r3*100:+7.0f}% (dd {d3*100:4.0f}%, sh {sh3:.2f})  n={len(tr)}')


if __name__ == "__main__":
    main()
