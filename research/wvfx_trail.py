"""Does the trailing exit (or the bear filter) rescue the WVF setup? (research)

Standalone copy of the wvfx portfolio loop with an optional Chandelier trail,
so an experimental knob cannot leak into the shared research simulator.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "research")

import wvfx_reanalyze as wr
from bot.scanlib import load_scan_bars

COINS = wr.SYMS


def load(sym: str) -> pd.DataFrame:
    bars = load_scan_bars(sym, "4h", source="binance", days=730)
    df = pd.DataFrame({
        "ts": pd.to_datetime([b.ts for b in bars], utc=True),
        "open": [b.open for b in bars], "high": [b.high for b in bars],
        "low": [b.low for b in bars], "close": [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })
    return df.sort_values("ts").reset_index(drop=True)


def feats() -> dict[str, pd.DataFrame]:
    out = {s: wr.features(load(s)) for s in COINS}
    for df in out.values():
        sma = df["close"].rolling(1200, min_periods=1200).mean()  # 200 days of 4h
        df["bear"] = (df["close"] < sma).fillna(False).astype(bool)
    return out


def rule_deployed(s, i, row):
    return bool(row["green_event"]) and bool(row["over_5"]) and bool(row["cross_up"])


@dataclass
class Pos:
    qty: float
    entry: float
    stop: float
    target: float
    entry_i: int
    risk_unit: float
    peak: float


def simulate(f, *, stop_mult=2.0, rr=2.0, max_hold=40, risk_frac=0.015,
             max_pos=3, max_pos_pct=0.34, fee_bps=10.0, bear=False, trail=None):
    syms = list(f)
    n = len(f[syms[0]])
    ts = f[syms[0]]["ts"].to_numpy()
    cash = 1000.0
    pos: dict[str, Pos] = {}
    queue: list[tuple[str, float, float, float]] = []
    eq, trades = [], []

    def mark(i):
        return cash + sum(p.qty * float(f[s]["close"].iat[i]) for s, p in pos.items())

    for i in range(300, n - 1):
        for s, stop, target, _k in queue:
            if s in pos or len(pos) >= max_pos:
                continue
            row = f[s].iloc[i]
            entry = float(row["open"])
            if entry <= stop:
                continue
            val = mark(i)
            risk_unit = entry - stop
            qty = min(risk_frac * val / risk_unit, max_pos_pct * val / entry,
                      cash / (entry * (1 + fee_bps / 1e4)))
            if qty * entry < 10:
                continue
            cash -= qty * entry * (1 + fee_bps / 1e4)
            pos[s] = Pos(qty, entry, stop, target, i, risk_unit, entry)
        queue = []

        for s in list(pos):
            p = pos[s]
            row = f[s].iloc[i]
            o, hi, lo, cl = (float(row[c]) for c in ("open", "high", "low", "close"))
            px = None
            if lo <= p.stop:
                px = min(o, p.stop)
            elif p.target is not None and hi >= p.target:
                px = max(o, p.target)
            elif i - p.entry_i >= max_hold:
                px = cl
            if px is not None:
                cash += p.qty * px * (1 - fee_bps / 1e4)
                trades.append((px - p.entry) / p.risk_unit)
                del pos[s]
                continue
            if trail is not None:
                atr = float(row["atr"])
                if np.isfinite(atr) and atr > 0:
                    p.peak = max(p.peak, hi)
                    cand = p.peak - trail * atr
                    if cand > p.stop:
                        p.stop = cand

        eq.append((ts[i], mark(i)))

        if len(pos) < max_pos:
            for s in syms:
                if s in pos:
                    continue
                row = f[s].iloc[i]
                if not rule_deployed(s, i, row):
                    continue
                if bear and not bool(row["bear"]):
                    continue
                atr = float(row["atr"])
                if not np.isfinite(atr) or atr <= 0:
                    continue
                close = float(row["close"])
                stop = close - stop_mult * atr
                target = None if trail is not None else close + rr * (close - stop)
                queue.append((s, stop, target, float(row["k"])))

    for s, p in list(pos.items()):
        cash += p.qty * float(f[s]["close"].iat[n - 1]) * (1 - fee_bps / 1e4)
    eq.append((ts[n - 1], cash))
    series = pd.Series(dict(eq)).astype(float)
    return series, np.array(trades)


def show(name, series, rs):
    r = series.pct_change().dropna()
    ret = series.iloc[-1] / 1000.0 - 1.0
    dd = float((series / series.cummax() - 1.0).min())
    sharpe = r.mean() / r.std() * np.sqrt(365 * 6) if r.std() > 0 else float("nan")
    print("%-34s %+7.1f%% %6.1f%% %6.2f %5d %+7.3f %5.0f%%" % (
        name, ret * 100, dd * 100, sharpe, len(rs),
        rs.mean() if len(rs) else float("nan"), (rs > 0).mean() * 100 if len(rs) else float("nan")))


def main():
    f = feats()
    print("%-34s %7s %6s %6s %5s %7s %5s" % ("variant", "ret", "dd", "sharpe", "n", "avgR", "win"))
    series, rs = simulate(f)
    show("fixed 2R target, no bear", series, rs)
    series, rs = simulate(f, bear=True)
    show("fixed 2R target + bear filter", series, rs)
    for tr in (1.5, 2.5, 3.5):
        series, rs = simulate(f, trail=tr)
        show("trail %.1f ATR, no bear" % tr, series, rs)
        series, rs = simulate(f, trail=tr, bear=True)
        show("trail %.1f ATR + bear filter" % tr, series, rs)


if __name__ == "__main__":
    main()