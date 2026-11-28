"""Fresh (independent) event study of William Vix Fix + Stochastic confluence."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, 'src')
from bot.analysis import stochastic, williams_vix_fix
from bot import indicators as ind

DATA = Path('data/crypto')
SYMS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']


def load(sym: str, tf: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f'{sym}_{tf}.csv')
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.drop(columns=['timestamp']).sort_values('ts').reset_index(drop=True)
    cutoff = df['ts'].max() - pd.DateOffset(years=2)
    return df[df['ts'] >= cutoff].reset_index(drop=True)


def features(df: pd.DataFrame, **wvf_kw) -> pd.DataFrame:
    out = df.copy()
    w = williams_vix_fix(out, **wvf_kw)
    s = stochastic(out)
    out['wvf'] = w['wvf']
    out['green'] = w['green'].fillna(False).astype(bool)
    out['green_event'] = out['green'] & ~out['green'].shift(1, fill_value=False)
    out['k'] = s['k']
    out['d'] = s['d']
    out['cross_up'] = (out['k'] >= out['d']) & (out['k'].shift(1) < out['d'].shift(1))
    out['cross_up'] = out['cross_up'].fillna(False)
    out['oversold'] = out['k'] < 20
    out['atr'] = ind.atr(out['high'], out['low'], out['close'], 14)
    out['ema50'] = ind.ema(out['close'], 50)
    out['ema200'] = ind.ema(out['close'], 200)
    for n in (2, 3, 5, 10):
        out[f'green_{n}'] = out['green'].rolling(n, min_periods=1).max().astype(bool)
        out[f'over_{n}'] = out['oversold'].rolling(n, min_periods=1).max().astype(bool)
    return out


def all_feats(tf: str, **wvf_kw) -> dict[str, pd.DataFrame]:
    return {s: features(load(s, tf), **wvf_kw) for s in SYMS}


def barrier_r(df: pd.DataFrame, i: int, stop_mult: float, rr: float, h: int):
    """Entry next open; conservative intrabar stop-first. Returns R or None."""
    if i + 1 >= len(df):
        return None
    atr = df['atr'].iat[i]
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df['open'].iat[i + 1])
    stop = entry - stop_mult * atr
    target = entry + rr * stop_mult * atr
    if stop <= 0:
        return None
    last = min(i + h, len(df) - 1)
    for j in range(i + 1, last + 1):
        o, hi, lo = float(df['open'].iat[j]), float(df['high'].iat[j]), float(df['low'].iat[j])
        if lo <= stop:
            px = min(o, stop)
            return (px - entry) / (entry - stop)
        if hi >= target:
            px = max(o, target)
            return (px - entry) / (entry - stop)
    px = float(df['close'].iat[last])
    return (px - entry) / (entry - stop)


def collect(tf: str, mask_fn, stop_mult=2.0, rr=1.5, h=40):
    rows = []
    for sym, df in all_feats(tf).items():
        m = mask_fn(df)
        for i in np.flatnonzero(m.to_numpy()):
            r = barrier_r(df, int(i), stop_mult, rr, h)
            if r is not None:
                rows.append(dict(sym=sym, ts=df['ts'].iat[i], r=r, year=df['ts'].iat[i].year))
    return pd.DataFrame(rows)


def summarize(name: str, ev: pd.DataFrame) -> str:
    if ev.empty:
        return f'{name:52s} n=0'
    wr = (ev.r > 0).mean()
    return (f'{name:52s} n={len(ev):5d} win={wr*100:5.1f}% '
            f'E[R]={ev.r.mean():+.3f} med={ev.r.median():+.3f} '
            f'pf={ev.r[ev.r>0].sum()/abs(ev.r[ev.r<=0].sum()) if (ev.r<=0).any() else np.inf:5.2f}')


def event_mask(df: pd.DataFrame, recency: int, require_over: bool, cross: bool = True):
    green = df['green_event'].rolling(recency, min_periods=1).max().astype(bool) if recency > 1 else df['green_event']
    m = green & df['cross_up']
    if require_over:
        m &= df['over_' + str(max(recency, 5))] if recency <= 5 else df['over_5']
    return m


def detail(tf: str, recency: int, require_over: bool, stop=2.0, rr=1.5, h=40):
    ev = collect(tf, lambda d: event_mask(d, recency, require_over), stop_mult=stop, rr=rr, h=h)
    print(f'\n-- recency={recency} require_over={require_over} stop={stop} rr={rr} h={h} --')
    print(summarize('pooled', ev))
    if ev.empty:
        return
    by_year = ev.groupby('year').r.agg(['count', 'mean', lambda x: (x > 0).mean()])
    by_year.columns = ['n', 'E[R]', 'win']
    print(by_year.round(3).to_string())
    by_coin = ev.groupby('sym').r.agg(['count', 'mean', lambda x: (x > 0).mean()])
    by_coin.columns = ['n', 'E[R]', 'win']
    print(by_coin.round(3).to_string())


def sensitivity(tf: str, recency: int, require_over: bool):
    print(f'\n-- geometry sensitivity recency={recency} over={require_over} --')
    for stop in (1.5, 2.0, 2.5, 3.0):
        for rr in (0.75, 1.0, 1.25, 1.5, 2.0, 2.5):
            ev = collect(tf, lambda d: event_mask(d, recency, require_over), stop_mult=stop, rr=rr, h=40)
            print(summarize(f'stop={stop} rr={rr}', ev))


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] == 'detail':
    tf = sys.argv[1]
    for rec in (1, 2, 3, 5, 8):
        detail(tf, rec, True)
    detail(tf, 1, False)
    sensitivity(tf, 1, True)


if __name__ == '__main__':
    tf = sys.argv[1] if len(sys.argv) > 1 else '4h'
    print(f'=== {tf} 3y window ===')
    feats = all_feats(tf)
    for sym, df in feats.items():
        print(f'{sym}: {len(df)} bars {df.ts.min().date()} -> {df.ts.max().date()}')
    print()
    variants = {
        'green & over & k>=d (all same bar)': lambda d: d.green & d.oversold & (d.k >= d.d),
        'green_5 & over_5 & k>=d (current impl)': lambda d: d.green_5 & d.over_5 & (d.k >= d.d),
        'green_5 & over_5 & cross_up': lambda d: d.green_5 & d.over_5 & d.cross_up,
        'green_3 & over_3 & cross_up': lambda d: d.green_3 & d.over_3 & d.cross_up,
        'green_2 & over_2 & cross_up': lambda d: d.green_2 & d.over_2 & d.cross_up,
        'green_event & over_5 & cross_up': lambda d: d.green_event & d.over_5 & d.cross_up,
        'green_event & over & cross_up': lambda d: d.green_event & d.oversold & d.cross_up,
        'green & over & cross_up': lambda d: d.green & d.oversold & d.cross_up,
        'green_event only': lambda d: d.green_event,
        'cross_up & over only': lambda d: d.cross_up & d.oversold,
    }
    for name, fn in variants.items():
        ev = collect(tf, fn)
        print(summarize(name, ev))
    print()
    # robustness of the most literal trigger across exit geometry
    base = 'green_5 & over_5 & k>=d (current impl)'
    for stop in (1.5, 2.0, 2.5, 3.0):
        for rr in (0.5, 1.0, 1.5, 2.0, 3.0):
            ev = collect(tf, variants[base], stop_mult=stop, rr=rr, h=40)
            print(summarize(f'current impl stop={stop} rr={rr}', ev))