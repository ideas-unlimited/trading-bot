"""Take the last-year winners and look at other periods (reality check)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'research'))

from bot import indicators as ind  # noqa: E402
from fastsim import simulate  # noqa: E402
from year_search import COINS, clean, sma  # noqa: E402


def load_all(coin, tf):
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_{tf}.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    return d.reset_index(drop=True)


def cross_signals(c, f, s, regime=0):
    fast, slow = sma(c, f), sma(c, s)
    pf, ps = np.roll(fast, 1), np.roll(slow, 1)
    e = (fast > slow) & (pf <= ps)
    x = (fast < slow) & (pf >= ps)
    warm = s
    if regime:
        e &= c > sma(c, regime)
        warm = max(warm, regime)
    return clean(e, warm + 1), clean(x, warm + 1)


def mom_signals(c, n, regime):
    mom = c / pd.Series(c).shift(n).to_numpy() - 1.0
    e = mom > 0
    if regime:
        e &= c > sma(c, regime)
    return clean(e, max(n, regime or 0) + 1), clean(mom < 0, n)


def run(coin_data, sig_fn, ex, periods):
    curves = []
    per_coin = {}
    for coin, d in coin_data.items():
        c = d.close.to_numpy(float)
        e, x = sig_fn(coin, d)
        atr = ind.atr(d.high, d.low, d.close, 14).to_numpy(float)
        eq, tr, bi = simulate(
            d.open.to_numpy(float), d.high.to_numpy(float), d.low.to_numpy(float), c,
            atr, e, x, **ex,
        )
        s = pd.Series(eq, index=pd.DatetimeIndex(d.ts))
        curves.append(s)
        per_coin[coin] = s
    port = pd.concat(curves, axis=1).ffill().mean(axis=1)
    out = {}
    for name, (a, b) in periods.items():
        pw = port[(port.index >= a) & (port.index <= b)]
        if len(pw) < 10:
            continue
        ret = pw.iloc[-1] / pw.iloc[0] - 1
        dd = float((pw / pw.cummax() - 1).min())
        daily = pw.resample('1D').last().dropna()
        dr = daily.pct_change().dropna()
        sharpe = float(dr.mean() / dr.std() * np.sqrt(365)) if dr.std() > 0 else 0.0
        out[name] = (ret, dd, sharpe)
    return out, per_coin


def main():
    tf = '4h'
    data = {c: load_all(c, tf) for c in COINS}
    periods = {
        'last 12m': (pd.Timestamp('2025-11-26', tz='UTC'), pd.Timestamp('2026-11-26', tz='UTC')),
        'prior 12m': (pd.Timestamp('2024-11-26', tz='UTC'), pd.Timestamp('2025-11-26', tz='UTC')),
        '2022 bear': (pd.Timestamp('2022-01-01', tz='UTC'), pd.Timestamp('2022-12-31', tz='UTC')),
        '2021 bull': (pd.Timestamp('2021-01-01', tz='UTC'), pd.Timestamp('2021-12-31', tz='UTC')),
        'full': (pd.Timestamp('2019-01-01', tz='UTC'), pd.Timestamp('2026-11-26', tz='UTC')),
    }

    cfgs = {
        'A cross30/60 trail2.5 stop1.5': (lambda coin, d: cross_signals(d.close.to_numpy(float), 30, 60),
                                          dict(trail_k=2.5, target_r=None, stop_atr=1.5)),
        'B cross30/60 trail3.0 stop1.5': (lambda coin, d: cross_signals(d.close.to_numpy(float), 30, 60),
                                          dict(trail_k=3.0, target_r=None, stop_atr=1.5)),
        'C cross30/60 cross stop2.0': (lambda coin, d: cross_signals(d.close.to_numpy(float), 30, 60),
                                        dict(trail_k=None, target_r=None, stop_atr=2.0)),
        'D momentum90>0 & 200d cross1.5': (lambda coin, d: mom_signals(d.close.to_numpy(float), 90, 1200),
                                            dict(trail_k=None, target_r=None, stop_atr=1.5)),
        'E cross30/50 cross stop2.5': (lambda coin, d: cross_signals(d.close.to_numpy(float), 30, 50),
                                        dict(trail_k=None, target_r=None, stop_atr=2.5)),
    }

    for name, (fn, ex) in cfgs.items():
        res, _ = run(data, fn, ex, periods)
        print(f'\n{name}')
        for k, (ret, dd, sh) in res.items():
            print(f'  {k:10s} ret {ret*100:+7.1f}%  dd {dd*100:6.1f}%  sharpe {sh:5.2f}')

    # per-coin last 12m for the top two
    print('\nper-coin, last 12m:')
    for name in list(cfgs)[:2]:
        fn, ex = cfgs[name]
        _, pc = run(data, fn, ex, periods)
        vals = []
        for coin, s in pc.items():
            w = s[(s.index >= periods['last 12m'][0])]
            vals.append(f'{coin[:3]} {(w.iloc[-1]/w.iloc[0]-1)*100:+5.1f}%')
        print(' ', name, ' '.join(vals))


if __name__ == "__main__":
    main()
