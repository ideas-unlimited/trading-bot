"""Cross-sectional momentum rotation - the "rank the coins, hold the strong"
family from the Finelo list, which the per-symbol studies never tested.

One year, long-only spot, weekly or daily rebalance: score every coin by its
return over the lookback, optionally require the coin to be above its own
100-day average, hold the top K equal weight, rest in cash. Decisions use data
up to the close, fills happen at the next bar open, costs 10 bps + 5 bps on the
turnover of each rebalance. IS = first 60% of the year, OOS = last 40%.

    python research/rotation.py
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import LOAD_DAYS, WINDOW_DAYS, universe  # noqa: E402
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

COST = 0.0015  # 10 bps fee + 5 bps slippage, one side
MIN_BARS = 1200


def panel(tf: str = "4h") -> pd.DataFrame:
    frames = {}
    for sym in universe():
        try:
            bars = load_scan_bars(sym, tf, source="binance", days=LOAD_DAYS + 120)
        except Exception:
            continue
        if len(bars) < MIN_BARS:
            print(f"  drop {sym}: {len(bars)} bars")
            continue
        f = bars_to_frame(bars)
        frames[sym] = f.set_index("ts")[["open", "close"]].astype(float)
    cls = pd.concat({s: f["close"] for s, f in frames.items()}, axis=1).sort_index()
    opn = pd.concat({s: f["open"] for s, f in frames.items()}, axis=1).sort_index()
    cls = cls.dropna(axis=1, thresh=int(len(cls) * 0.95))
    opn = opn[cls.columns]
    cls = cls.ffill()
    opn = opn.ffill()
    return cls, opn


def simulate(cls: pd.DataFrame, opn: pd.DataFrame, lookback: int, top_k: int,
             rebal: int, ma: int | None) -> dict:
    idx = cls.index
    start_ts = idx[-1] - pd.Timedelta(days=WINDOW_DAYS)
    rbars = [i for i in range(0, len(idx) - rebal, rebal) if idx[i] >= start_ts]
    if len(rbars) < 8:
        return {}
    w_prev = pd.Series(0.0, index=cls.columns)
    eq = [1.0]
    eq_ts = [idx[rbars[0]]]
    turn = 0.0
    for a, b in zip(rbars[:-1], rbars[1:]):
        score = cls.iloc[a] / cls.iloc[a - lookback] - 1.0
        ok = score.notna()
        if ma is not None and a - ma >= 0:
            ok &= cls.iloc[a] > cls.iloc[a - ma].where(cls.iloc[a - ma] > 0)
        s = score[ok].sort_values(ascending=False).head(top_k)
        w = pd.Series(0.0, index=cls.columns)
        if len(s):
            w[s.index] = 1.0 / top_k
        t = float((w - w_prev).abs().sum())
        turn += t
        grow = (opn.iloc[b] / opn.iloc[a]) - 1.0
        port = float((w * grow.fillna(0.0)).sum() - t * COST)
        eq.append(eq[-1] * (1.0 + port))
        eq_ts.append(idx[b])
        w_prev = w
    eq = pd.Series(eq, index=eq_ts)
    ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    mid = eq.index[0] + (eq.index[-1] - eq.index[0]) * 0.6
    h1 = eq[eq.index < mid]
    h2 = eq[eq.index >= mid]
    is_ret = float(h1.iloc[-1] / h1.iloc[0] - 1.0) if len(h1) > 1 else float("nan")
    oos_ret = float(h2.iloc[-1] / h2.iloc[0] - 1.0) if len(h2) > 1 else float("nan")
    score = min(is_ret, oos_ret) if np.isfinite(is_ret) and np.isfinite(oos_ret) else float("nan")
    return {"ret": ret, "dd": dd, "is_ret": is_ret, "oos_ret": oos_ret,
            "score": score, "turnover": turn, "rebalances": len(eq) - 1,
            "eq": eq}


def main() -> None:
    cls, opn = panel("4h")
    print(f"universe: {cls.shape[1]} coins, {len(cls)} bars")
    start_ts = cls.index[-1] - pd.Timedelta(days=WINDOW_DAYS)
    win = cls.index >= start_ts
    ew = (cls[win] / cls[win].iloc[0]).mean(axis=1)
    bench = {
        "equal-weight buy&hold": float(ew.iloc[-1] - 1.0),
        "BTC buy&hold": float(cls["BTC/USDT"][win].iloc[-1] / cls["BTC/USDT"][win].iloc[0] - 1.0) if "BTC/USDT" in cls else float("nan"),
    }
    print("benchmarks:", {k: f"{v:+.1%}" for k, v in bench.items()})

    bpy = 365 * 24 / 4
    hours = {"1d": 6, "3d": 18, "7d": 42}
    looks = {"20d": 120, "40d": 240, "60d": 360}
    rows = []
    for rebal_name, lb_name, k, ma in product(hours, looks, (1, 2, 3, 5), (None, 600)):
        r = simulate(cls, opn, lookback=looks[lb_name], top_k=k,
                     rebal=hours[rebal_name], ma=ma)
        if not r:
            continue
        rows.append({"rebal": rebal_name, "lookback": lb_name, "top_k": k,
                     "ma": "100d" if ma else "none", "ret": r["ret"], "dd": r["dd"],
                     "is_ret": r["is_ret"], "oos_ret": r["oos_ret"], "score": r["score"],
                     "turnover": r["turnover"], "rebalances": r["rebalances"]})
    d = pd.DataFrame(rows)
    d.to_csv(HERE / "rotation.csv", index=False)
    cols = ["rebal", "lookback", "top_k", "ma", "ret", "dd", "is_ret", "oos_ret", "score", "turnover"]
    show = d.sort_values("score", ascending=False).head(15)[cols].copy()
    for c in ("ret", "dd", "is_ret", "oos_ret", "score"):
        show[c] = show[c].map(lambda v: f"{v:+.1%}")
    show["turnover"] = show["turnover"].map(lambda v: f"{v:.1f}x")
    print("\n=== top 15 by IS score ===")
    print(show.to_string(index=False))
    show2 = d.sort_values("ret", ascending=False).head(10)[cols].copy()
    for c in ("ret", "dd", "is_ret", "oos_ret", "score"):
        show2[c] = show2[c].map(lambda v: f"{v:+.1%}")
    show2["turnover"] = show2["turnover"].map(lambda v: f"{v:.1f}x")
    print("\n=== top 10 by full-window return ===")
    print(show2.to_string(index=False))
    print(f"\n{d.ret.gt(0).mean():.0%} of {len(d)} rotation configs positive; "
          f"{d.score.gt(0).mean():.0%} positive in both halves")


if __name__ == "__main__":
    main()