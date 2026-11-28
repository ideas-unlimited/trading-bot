"""Fast long-only bar simulator for grid search.

Mirrors the production Engine rules: signal on bar i, fill at open i+1,
stop/target checked intrabar (stop first), trail ratcheted after the exit
check, 10 bps fees, slippage on both sides, risk-based sizing.

The production Engine remains the source of truth; this exists because it
runs ~100x faster and the search needs thousands of runs.  A handful of
configs are checked against Engine.run() in check_fastsim.py.
"""

from __future__ import annotations

import numpy as np


def simulate(
    o, h, l, c, atr, entry_sig, exit_sig,
    trail_k=None, stop_atr=2.5, target_r=None,
    risk=0.02, fee=0.001, slip=0.0005, cash0=1000.0, cap=0.95,
):
    """Return (equity array, list of trade R multiples, bars in market)."""
    n = len(c)
    cash = cash0
    qty = 0.0
    entry_px = 0.0
    stop = 0.0
    risk0 = 0.0
    take = np.nan
    peak = 0.0
    pending = 0  # 1 buy, -1 sell
    eq = np.empty(n)
    trades: list[float] = []
    bars_in = 0

    for i in range(n):
        oi, hi, li, ci = o[i], h[i], l[i], c[i]

        # 1. pending order fills at this bar's open
        if pending == 1:
            px = oi * (1.0 + slip)
            equity = cash
            if 0.0 < stop < px:
                q = equity * risk / (px - stop)
            else:
                q = equity * cap / px
            q = min(q, equity * cap / px, cash / (px * (1.0 + fee)))
            if q * px >= 10.0:
                cash -= q * px * (1.0 + fee)
                qty = q
                entry_px = px
                risk0 = px - stop
                peak = px
            pending = 0
        elif pending == -1:
            if qty > 0:
                px = oi * (1.0 - slip)
                cash += qty * px * (1.0 - fee)
                trades.append((px - entry_px) / risk0 if risk0 > 0 else 0.0)
                qty = 0.0
            pending = 0

        # 2. intrabar stop / target
        if qty > 0:
            if li <= stop:
                px = min(oi, stop) * (1.0 - slip)
                cash += qty * px * (1.0 - fee)
                trades.append((px - entry_px) / risk0 if risk0 > 0 else 0.0)
                qty = 0.0
            elif not np.isnan(take) and hi >= take:
                px = max(oi, take) * (1.0 - slip)
                cash += qty * px * (1.0 - fee)
                trades.append((px - entry_px) / risk0 if risk0 > 0 else 0.0)
                qty = 0.0

        # 3. trail ratchets only after the exit check
        if qty > 0 and trail_k:
            if hi > peak:
                peak = hi
            a = atr[i]
            if a == a and a > 0:
                cand = peak - trail_k * a
                if cand > stop:
                    stop = cand

        eq[i] = cash + qty * ci
        if qty > 0:
            bars_in += 1

        # 4. new signal, filled next bar
        if qty == 0 and entry_sig[i]:
            a = atr[i]
            if a == a and a > 0 and ci - stop_atr * a > 0:
                stop = ci - stop_atr * a
                take = ci + target_r * stop_atr * a if target_r else np.nan
                pending = 1
        elif qty > 0 and exit_sig[i]:
            pending = -1

    if qty > 0:
        px = c[-1] * (1.0 - slip)
        cash += qty * px * (1.0 - fee)
        trades.append((px - entry_px) / risk0 if risk0 > 0 else 0.0)
    eq[-1] = cash
    return eq, trades, bars_in

def simulate_book(data, entry_sigs, exit_sigs, score, trail_k=None, stop_atr=2.5,
                  target_r=None, risk=0.02, fee=0.001, slip=0.0005,
                  cash0=1000.0, cap=0.95, max_pos=1):
    """Shared-capital book: one wallet, at most max_pos concurrent longs.

    ``data`` maps coin -> dict(o,h,l,c,atr,ts).  Signals are indexed by bar.
    Buys are ranked by ``score`` (higher first) when more than one fires.
    """
    coins = list(data)
    n = len(data[coins[0]]['c'])
    cash = cash0
    pos = {}
    pending_sell: list[str] = []
    pending_buy: list[str] = []
    pending_stop: dict[str, float] = {}
    eq = np.empty(n)
    trades: list[float] = []
    bars_in = 0

    def value(i: int) -> float:
        v = cash
        for c, p in pos.items():
            v += p['qty'] * data[c]['c'][i]
        return v

    def close_pos(coin: str, i: int, px: float, why: str) -> None:
        nonlocal cash
        p = pos.pop(coin)
        fill = px * (1.0 - slip)
        cash += p['qty'] * fill * (1.0 - fee)
        trades.append((fill - p['entry']) / p['risk0'] if p['risk0'] > 0 else 0.0)

    for i in range(n):
        # 1. open: sells first, then buys
        for coin in list(pending_sell):
            if coin in pos:
                close_pos(coin, i, data[coin]['o'][i], 'signal')
        pending_sell = []

        for coin in pending_buy:
            if len(pos) >= max_pos or coin in pos:
                continue
            stop = pending_stop.get(coin, 0.0)
            px = data[coin]['o'][i] * (1.0 + slip)
            if stop <= 0 or stop >= px:
                continue
            equity = value(i)
            qty = min(risk * equity / (px - stop), cap * equity / px, cash / (px * (1.0 + fee)))
            if qty * px < 10.0:
                continue
            cash -= qty * px * (1.0 + fee)
            pos[coin] = {'qty': qty, 'entry': px, 'stop': stop, 'risk0': px - stop,
                         'take': px + target_r * (px - stop) if target_r else np.nan,
                         'peak': px}
        pending_buy = []

        # 2. intrabar stops / targets
        for coin in list(pos):
            p = pos[coin]
            o, h, l = data[coin]['o'][i], data[coin]['h'][i], data[coin]['l'][i]
            if l <= p['stop']:
                close_pos(coin, i, min(o, p['stop']), 'stop')
            elif not np.isnan(p['take']) and h >= p['take']:
                close_pos(coin, i, max(o, p['take']), 'target')

        # 3. trail
        if trail_k:
            for coin, p in pos.items():
                h = data[coin]['h'][i]
                if h > p['peak']:
                    p['peak'] = h
                a = data[coin]['atr'][i]
                if a == a and a > 0:
                    cand = p['peak'] - trail_k * a
                    if cand > p['stop']:
                        p['stop'] = cand

        eq[i] = value(i)
        if pos:
            bars_in += 1

        # 4. signals for next bar
        if len(pos) < max_pos:
            cands = [c for c in coins if c not in pos and entry_sigs[c][i]]
            if cands:
                cands.sort(key=lambda c: score[c][i], reverse=True)
                take = cands[: max_pos - len(pos)]
                for c in take:
                    a = data[c]['atr'][i]
                    if a == a and a > 0 and data[c]['c'][i] - stop_atr * a > 0:
                        pending_buy.append(c)
                        pending_stop[c] = data[c]['c'][i] - stop_atr * a
        for c in pos:
            if exit_sigs[c][i]:
                pending_sell.append(c)

    for coin in list(pos):
        close_pos(coin, n - 1, data[coin]['c'][n - 1], 'end')
    eq[-1] = cash
    return eq, trades, bars_in / n
