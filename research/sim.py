
"""Portfolio simulator: conviction-gated longs, ATR stop/target, no fees."""
from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(p: pd.DataFrame, *, threshold: float = 0.70, stop_mult: float = 2.0,
             rr: float = 1.5, horizon: int = 40, risk: float = 0.01,
             max_pos: int = 2, max_pos_pct: float = 0.95, start_cash: float = 1000.0,
             cooldown: int = 3, tf: str = "1d", fee_bps: float = 0.0) -> dict:
    """Bar-by-bar portfolio sim.

    Signal at close of bar i -> fill at open of bar i+1 (no lookahead).
    Stop is checked before target when both fall inside one bar (pessimistic).
    """
    df = p.sort_values(["ts", "sym"]).reset_index(drop=True)
    dates = np.sort(df["ts"].unique())
    by_date = {d: g for d, g in df.groupby("ts")}
    cash = start_cash
    pos: dict[str, dict] = {}
    entry_queue: list[tuple[str, float]] = []
    last_exit: dict[str, int] = {}
    trades: list[dict] = []
    eq_rows: list[dict] = []
    fee = fee_bps / 10_000.0

    for ti, d in enumerate(dates):
        g = by_date[d]
        rows = {r.sym: r for r in g.itertuples()}

        # 1) fills queued from the previous close, at this bar's open
        still: list[tuple[str, float]] = []
        for sym, p_at_signal in entry_queue:
            r = rows.get(sym)
            if r is None:
                continue
            if sym in pos or len(pos) >= max_pos:
                continue
            entry = float(r.open)
            stop = entry - stop_mult * float(r.atr)
            if stop <= 0 or entry <= 0:
                continue
            equity = cash + sum(x["qty"] * float(rows[s].close) for s, x in pos.items() if s in rows)
            qty = risk * equity / (entry - stop)
            qty = min(qty, max_pos_pct * equity / entry)
            notional = qty * entry
            if notional > cash:  # no leverage
                qty = cash / entry if cash > 0 else 0.0
                notional = qty * entry
            if notional < 20:
                continue
            cash -= notional + notional * fee
            pos[sym] = {"qty": qty, "entry": entry, "stop": stop,
                        "target": entry + rr * stop_mult * float(r.atr),
                        "bars": 0, "p": p_at_signal, "open_ts": d}

        # 2) exits intrabar
        for sym in list(pos):
            r = rows.get(sym)
            if r is None:
                continue
            x = pos[sym]
            x["bars"] += 1
            lo, hi, op = float(r.low), float(r.high), float(r.open)
            px = None
            reason = ""
            if lo <= x["stop"]:
                px, reason = min(op, x["stop"]), "stop"
            elif hi >= x["target"]:
                px, reason = max(op, x["target"]), "target"
            elif x["bars"] >= horizon:
                px, reason = float(r.close), "time"
            if px is not None:
                proceeds = x["qty"] * px
                cash += proceeds - proceeds * fee
                r_mult = (px - x["entry"]) / (x["entry"] - x["stop"])
                trades.append({"sym": sym, "open_ts": x["open_ts"], "close_ts": d,
                               "entry": x["entry"], "exit": px, "reason": reason,
                               "r": r_mult, "p": x["p"], "bars": x["bars"]})
                del pos[sym]
                last_exit[sym] = ti

        # 3) equity mark, then new signals at this close
        equity = cash + sum(x["qty"] * float(rows[s].close) for s, x in pos.items() if s in rows)
        if not np.isfinite(equity):
            equity = cash
        eq_rows.append({"ts": d, "equity": equity, "n_pos": len(pos)})

        entry_queue = []
        if len(pos) < max_pos:
            cand = g[g["p"] >= threshold].sort_values("p", ascending=False)
            for r in cand.itertuples():
                if r.sym in pos or len(pos) + len(entry_queue) >= max_pos:
                    continue
                if ti - last_exit.get(r.sym, -10_000) < cooldown:
                    continue
                if not np.isfinite(r.atr) or r.atr <= 0:
                    continue
                entry_queue.append((r.sym, float(r.p)))
                if len(entry_queue) + len(pos) >= max_pos:
                    break

    eq = pd.DataFrame(eq_rows).set_index("ts")["equity"]
    tr = pd.DataFrame(trades)
    return {"equity": eq, "trades": tr, "start": start_cash, "tf": tf}


def stats(res: dict, tf: str = "1d", bench: pd.Series | None = None) -> dict:
    eq = res["equity"]
    tr = res["trades"]
    bpy = 365.0 if tf == "1d" else 365.0 * 6
    if len(eq) < 2:
        return {}
    total = eq.iloc[-1] / res["start"] - 1
    yrs = len(eq) / bpy
    cagr = (eq.iloc[-1] / res["start"]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    r = eq.pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(bpy)) if r.std() > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    out = {"final": float(eq.iloc[-1]), "return": total, "cagr": cagr,
           "sharpe": sharpe, "maxdd": dd, "trades": int(len(tr)),
           "exposure": float(res.get("exposure", np.nan))}
    if len(tr):
        wins = tr[tr.r > 0]
        losses = tr[tr.r <= 0]
        out.update({"win_rate": len(wins) / len(tr),
                    "avg_r": float(tr.r.mean()),
                    "pf": float(wins.r.sum() / abs(losses.r.sum())) if len(losses) and losses.r.sum() != 0 else np.inf,
                    "avg_win_r": float(wins.r.mean()) if len(wins) else 0.0,
                    "avg_loss_r": float(losses.r.mean()) if len(losses) else 0.0,
                    "avg_bars": float(tr.bars.mean())})
    if bench is not None and len(bench) > 1:
        out["bench_return"] = float(bench.iloc[-1] / bench.iloc[0] - 1)
    return out
