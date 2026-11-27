"""Portfolio simulation for the WVF+Stoch event setup (research)."""
from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, 'src')
sys.path.insert(0, 'research')
import wvfx_reanalyze as wr

SYMS = wr.SYMS


@dataclass
class Pos:
    sym: str
    qty: float
    entry: float
    stop: float
    target: float
    entry_i: int
    risk_unit: float


def btc_regime(feats, ma_days=200):
    btc = feats['BTCUSDT'].set_index('ts')
    daily = btc['close'].resample('1D').last().dropna()
    sma = daily.rolling(ma_days, min_periods=ma_days).mean()
    bull = pd.Series(np.nan, index=daily.index, dtype=float)
    valid = sma.notna()
    bull[valid] = (daily[valid] > sma[valid]).astype(float)
    return bull.shift(1)


def simulate(feats, rule, *, stop_mult=2.0, rr=2.0, max_hold=40, exit_cross=False,
             risk_frac=0.01, max_pos=3, max_pos_pct=0.34, fee_bps=0.0,
             btc_bull=None, bear_only=False, rank='k', start=300):
    syms = list(feats)
    n = len(feats[syms[0]])
    data = {s: feats[s] for s in syms}
    ts = data[syms[0]]['ts'].to_numpy()
    cash = 1000.0
    pos: dict[str, Pos] = {}
    pending_entry: list[tuple[str, float, float, float, float, float]] = []  # sym, sig_close, stop, target, atr, k
    pending_exit: dict[str, str] = {}
    equity_curve = []
    trades = []
    entries = 0

    def mark(i):
        return cash + sum(p.qty * float(data[s]['close'].iat[i]) for s, p in pos.items())

    def mark_open(i):
        return cash + sum(p.qty * float(data[s]['open'].iat[i]) for s, p in pos.items())

    for i in range(start, n - 1):
        # execute queued exits at this open
        for s, why in list(pending_exit.items()):
            p = pos.pop(s, None)
            if p is None:
                continue
            px = float(data[s]['open'].iat[i]) * (1 - fee_bps / 1e4)
            cash += p.qty * px
            trades.append((p.sym, p.entry_i, i, px, (px - p.entry) / p.risk_unit, why))
        pending_exit.clear()
    
        # execute queued entries at this open
        if pending_entry:
            # rank candidates; lower k = more oversold
            pending_entry.sort(key=lambda t: t[5])
            for s, sig_close, stop, target, atr, kval in pending_entry:
                if s in pos or len(pos) >= max_pos:
                    continue
                entry = float(data[s]['open'].iat[i])
                if entry <= stop:
                    continue
                eq = mark_open(i)
                risk_amt = risk_frac * eq
                risk_unit = entry - stop
                qty = risk_amt / risk_unit
                qty = min(qty, max_pos_pct * eq / entry)
                qty = min(qty, cash / (entry * (1 + fee_bps / 1e4)))
                if qty * entry < 10:
                    continue
                cash -= qty * entry * (1 + fee_bps / 1e4)
                pos[s] = Pos(s, qty, entry, stop, target, i, risk_unit)
                entries += 1
            pending_entry.clear()

        # intrabar stops/targets + queued time/cross exits
        for s in list(pos):
            p = pos[s]
            row = data[s].iloc[i]
            o, hi, lo, cl = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            exit_px = None
            reason = ''
            if lo <= p.stop:
                exit_px = min(o, p.stop)
                reason = 'stop'
            elif hi >= p.target:
                exit_px = max(o, p.target)
                reason = 'target'
            elif exit_cross and pd.notna(row['k']) and pd.notna(row['d']) and row['k'] < row['d']:
                pending_exit[s] = 'cross'
                continue
            elif i - p.entry_i >= max_hold:
                pending_exit[s] = 'time'
                continue
            if exit_px is not None:
                cash += p.qty * exit_px * (1 - fee_bps / 1e4)
                trades.append((s, p.entry_i, i, exit_px, (exit_px - p.entry) / p.risk_unit, reason))
                del pos[s]

        equity_curve.append((ts[i], mark(i)))

        # signals at this close for next open
        if len(pos) < max_pos:
            cand = []
            for s in syms:
                if s in pos or s in {x[0] for x in pending_entry}:
                    continue
                row = data[s].iloc[i]
                if not rule(s, i, row, btc_bull, bear_only):
                    continue
                atr = float(row['atr'])
                if not np.isfinite(atr) or atr <= 0:
                    continue
                sig_close = float(row['close'])
                stop = sig_close - stop_mult * atr
                target = sig_close + rr * (sig_close - stop)
                if stop <= 0 or target <= stop:
                    continue
                cand.append((s, sig_close, stop, target, atr, float(row['k'])))
            pending_entry.extend(cand)

    # close anything left at last close
    last = n - 1
    for s, p in list(pos.items()):
        px = float(data[s]['close'].iat[last]) * (1 - fee_bps / 1e4)
        cash += p.qty * px
        trades.append((s, p.entry_i, last, px, (px - p.entry) / p.risk_unit, 'end'))
    eq = pd.Series(dict(equity_curve)).astype(float)
    tr = pd.DataFrame(trades, columns=['sym','entry_i','exit_i','exit','r','reason'])
    return eq, tr, entries


def stats(eq, tr, tf='4h'):
    if len(eq) < 2:
        return {}
    r = eq.pct_change().dropna()
    bpy = 365 * 6 if tf == '4h' else 365
    yrs = len(eq) / bpy
    total = eq.iloc[-1] / 1000.0 - 1
    cagr = (1 + total) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    win = (tr.r > 0).mean() if len(tr) else np.nan
    pf = tr[tr.r > 0].r.sum() / abs(tr[tr.r <= 0].r.sum()) if len(tr) and (tr.r <= 0).any() else np.nan
    return dict(final=eq.iloc[-1], ret=total, cagr=cagr, dd=dd,
                sharpe=r.mean() / r.std() * np.sqrt(bpy) if r.std() > 0 else np.nan,
                n=len(tr), win=win, avgR=tr.r.mean() if len(tr) else np.nan, pf=pf,
                avg_bars=((tr.exit_i - tr.entry_i).mean() if len(tr) else np.nan))