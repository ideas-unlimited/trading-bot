"""Search the last 365 days for the most profitable long-only rule.

Selection uses ONLY bars from the final year.  Every config is run on all
five coins as independent $1,000 sleeves, then the sleeves are averaged into
one portfolio so a lucky single coin cannot win the search.

Usage:  python research/year_search.py --tf 4h
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bot import indicators as ind  # noqa: E402
from fastsim import simulate  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COINS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'SOLUSDT']
END = pd.Timestamp('2026-11-26', tz='UTC')
START = END - pd.Timedelta(days=365)


def load(coin: str, tf: str, pre: int) -> pd.DataFrame:
    d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{coin}_{tf}.csv')
    d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    d = d[d.ts <= END].reset_index(drop=True)
    # keep every bar up to now; warmup happens before the window
    first = d.index[d.ts >= START][0]
    return d.iloc[max(0, first - pre):].reset_index(drop=True)


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n).mean().to_numpy()


def rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    return ind.rsi(pd.Series(x), n).to_numpy()


def shift1(a: np.ndarray) -> np.ndarray:
    out = np.empty_like(a)
    out[0] = np.nan
    out[1:] = a[:-1]
    return out


def clean(a: np.ndarray, n: int) -> np.ndarray:
    a = np.nan_to_num(a.astype(float))
    a[:n] = 0.0
    return a.astype(bool)


def entries_macross(c, f, s):
    fast, slow = sma(c, f), sma(c, s)
    up = (fast > slow) & (shift1(fast) <= shift1(slow))
    dn = (fast < slow) & (shift1(fast) >= shift1(slow))
    return clean(up, s), clean(dn, s)


def entries_breakout(c, h, n, filt):
    level = pd.Series(h).rolling(n).max().shift(1).to_numpy()
    e = c > level
    if filt:
        e &= c > sma(c, filt)
    return clean(e, max(n, filt or 0) + 1), clean(c < sma(c, n), n)


def entries_momentum(c, n, thr, filt):
    mom = c / pd.Series(c).shift(n).to_numpy() - 1.0
    e = mom > thr
    if filt:
        e &= c > sma(c, filt)
    return clean(e, max(n, filt or 0) + 1), clean(mom < 0, n)


def entries_dip(c, rsi_n, level, exit_l, filt):
    r = rsi(c, rsi_n)
    e = r < level
    if filt:
        e &= c > sma(c, filt)
    return clean(e, max(rsi_n, filt or 0) + 1), clean(r > exit_l, rsi_n)


def metrics(eq: np.ndarray, ts: pd.Series, trades: list[float], bars_in: int, n: int) -> dict:
    ret = eq[-1] / eq[0] - 1.0
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1.0).min())
    s = pd.Series(eq, index=pd.DatetimeIndex(ts))
    daily = s.resample('1D').last().dropna()
    dr = daily.pct_change().dropna()
    sharpe = float(dr.mean() / dr.std() * np.sqrt(365)) if len(dr) > 5 and dr.std() > 0 else 0.0
    wins = sum(1 for r in trades if r > 0)
    return {
        'ret': ret, 'dd': dd, 'sharpe': sharpe,
        'trades': len(trades), 'win': wins / len(trades) if trades else 0.0,
        'avg_r': float(np.mean(trades)) if trades else 0.0,
        'exposure': bars_in / n if n else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    tf = args.tf
    pre = 500 if tf == '4h' else 400
    out = Path(args.out or ROOT / 'runs' / f'year_search_{tf}.csv')

    data = {}
    for coin in COINS:
        d = load(coin, tf, pre)
        data[coin] = d

    # ---- entry specs ----------------------------------------------------
    specs: list[tuple[str, dict, np.ndarray, np.ndarray]] = []
    for f, s in itertools.product([5, 10, 15, 20, 30], [30, 50, 100, 150, 200]):
        if f >= s:
            continue
        specs.append(('cross', {'f': f, 's': s}, None, None))
    for n, filt in itertools.product([10, 20, 30, 55, 100], [0, 50, 100, 200]):
        specs.append(('breakout', {'n': n, 'filt': filt}, None, None))
    for n, thr, filt in itertools.product([10, 20, 40, 60], [0.0, 0.05, 0.10], [0, 100, 200]):
        specs.append(('momentum', {'n': n, 'thr': thr, 'filt': filt}, None, None))
    for lv, ex, filt in itertools.product([20, 25, 30, 35, 40], [55, 65, 75], [0, 100, 200]):
        specs.append(('dip', {'rsi_n': 14, 'level': lv, 'exit_l': ex, 'filt': filt}, None, None))

    exits = [
        ('cross_only', dict(trail_k=None, target_r=None, stop_atr=2.5)),
        ('trail2.0', dict(trail_k=2.0, target_r=None, stop_atr=2.5)),
        ('trail3.5', dict(trail_k=3.5, target_r=None, stop_atr=2.5)),
        ('trail5.0', dict(trail_k=5.0, target_r=None, stop_atr=2.5)),
        ('trail3.5stop3', dict(trail_k=3.5, target_r=None, stop_atr=3.0)),
        ('fixed2R', dict(trail_k=None, target_r=2.0, stop_atr=2.5)),
        ('trail3.5fix2R', dict(trail_k=3.5, target_r=2.0, stop_atr=2.5)),
    ]

    rows = []
    # cache signals per (family, params) per coin
    cache = {}
    total = len(specs) * len(exits)
    done = 0
    for fam, p, _, _ in specs:
        key = (fam, tuple(sorted(p.items())))
        if key not in cache:
            per = {}
            for coin, d in data.items():
                c = d.close.to_numpy(float)
                h = d.high.to_numpy(float)
                if fam == 'cross':
                    e, x = entries_macross(c, p['f'], p['s'])
                elif fam == 'breakout':
                    e, x = entries_breakout(c, h, p['n'], p['filt'])
                elif fam == 'momentum':
                    e, x = entries_momentum(c, p['n'], p['thr'], p['filt'])
                else:
                    e, x = entries_dip(c, p['rsi_n'], p['level'], p['exit_l'], p['filt'])
                per[coin] = (e, x)
            cache[key] = per
        per = cache[key]

        for ename, ex in exits:
            curves, all_tr, bars, n = [], [], 0, 0
            for coin, d in data.items():
                e, x = per[coin]
                c = d.close.to_numpy(float)
                atr = ind.atr(d.high, d.low, d.close, 14).to_numpy(float)
                eq, tr, bi = simulate(
                    d.open.to_numpy(float), d.high.to_numpy(float), d.low.to_numpy(float), c,
                    atr, e, x, **ex,
                )
                curves.append(pd.Series(eq, index=pd.DatetimeIndex(d.ts)))
                all_tr += tr
                bars += bi
                n += len(d)
            port = pd.concat(curves, axis=1).ffill().mean(axis=1)
            # evaluate only the final window
            mask = port.index >= START
            pw = port[mask]
            st = metrics(pw.to_numpy(float), pd.Series(pw.index), all_tr, bars, n)
            rows.append({'family': fam, **p, 'exit': ename, **ex, **st, 'tf': tf})
            done += 1
            if done % 200 == 0:
                print(f'  {done}/{total}', flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(out, index=False)
    print(f'wrote {out} ({len(df)} configs)')

    pool = df[df.trades >= 6].copy()
    cols = ['family', 'exit', 'ret', 'dd', 'sharpe', 'trades', 'win', 'avg_r', 'exposure']
    print('\nTOP 12 by return (>=6 trades):')
    print(pool.sort_values('ret', ascending=False).head(12)[cols + ['f', 's', 'n', 'filt', 'trail_k', 'stop_atr', 'level']].to_string(index=False))
    print('\nMEDIAN config:', pool[['ret', 'dd', 'sharpe', 'trades']].median().round(3).to_dict())


if __name__ == "__main__":
    main()
