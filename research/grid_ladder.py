"""Long-only grid ladder - the "grid" family, tested instead of assumed.

The article says a grid needs a bounded range and dies of inventory after a
breakout. This measures that on our 30 coins over one year: place buy levels
step% under a rolling anchor high, take profit step% above each fill, liquidate
everything if price falls through the whole ladder. Fills happen at the next
bar open (no intrabar limit orders invented), costs 10 bps + 5 bps per side.

    python research/grid_ladder.py
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import LOAD_DAYS, WINDOW_DAYS, universe  # noqa: E402
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

COST = 0.0015


def simulate(df: pd.DataFrame, anchor_bars: int, step: float, levels: int,
             tp: float, floor_buffer: float = 0.0) -> dict:
    """One ladder run. df: ts/open/high/low/close, ascending."""
    start = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    ts = df["ts"].to_numpy()
    cash, lots = 1.0, []  # lots: [entry, qty]
    per_level = 1.0 / levels
    pending = None
    eq_path = []
    floor = None
    for i in range(anchor_bars + 1, len(df)):
        # execute yesterday's decision at today's open
        if pending == "buy" and cash >= per_level - 1e-12 and o[i] > 0:
            qty = per_level / o[i]
            lots.append([o[i], qty])
            cash -= per_level * (1.0 + COST)
            pending = None
        elif pending == "flat":
            for entry, qty in lots:
                cash += qty * o[i] * (1.0 - COST)
            lots = []
            pending = None
        # sells fill at the target if the bar trades through it, else hold
        still = []
        for entry, qty in lots:
            tgt = entry * (1.0 + tp)
            if c[i] >= tgt or o[i] >= tgt:
                cash += qty * max(tgt, o[i]) * (1.0 - COST)
            else:
                still.append([entry, qty])
        lots = still
        # mark to market
        eq = cash + sum(q * c[i] for _, q in lots)
        eq_path.append((ts[i], eq))
        # decide for the next open
        hi = c[i - anchor_bars:i + 1].max()
        if not lots and cash >= per_level - 1e-12 and i - anchor_bars >= 0:
            floor = hi * (1.0 - step * levels)
            if c[i] <= hi * (1.0 - step) and c[i] > floor * (1.0 - floor_buffer):
                pending = "buy"
        elif lots and floor is not None and c[i] < floor * (1.0 - floor_buffer):
            pending = "flat"
        elif not lots and floor is not None and c[i] < floor:
            floor = None
    eqs = pd.Series([e for _, e in eq_path], index=pd.to_datetime([t for t, _ in eq_path]))
    eqs = eqs[eqs.index >= start]
    if len(eqs) < 10:
        return {}
    ret = float(eqs.iloc[-1] / eqs.iloc[0] - 1.0)
    dd = float((eqs / eqs.cummax() - 1.0).min())
    mid = eqs.index[0] + (eqs.index[-1] - eqs.index[0]) * 0.6
    h1, h2 = eqs[eqs.index < mid], eqs[eqs.index >= mid]
    is_ret = float(h1.iloc[-1] / h1.iloc[0] - 1.0) if len(h1) > 1 else float("nan")
    oos_ret = float(h2.iloc[-1] / h2.iloc[0] - 1.0) if len(h2) > 1 else float("nan")
    return {"ret": ret, "dd": dd, "is_ret": is_ret, "oos_ret": oos_ret,
            "score": min(is_ret, oos_ret)}


def main() -> None:
    rows = []
    for tf in ("4h", "1d"):
        h = 4 if tf == "4h" else 24
        for sym in universe():
            try:
                bars = load_scan_bars(sym, tf, source="binance", days=LOAD_DAYS)
            except Exception:
                continue
            if len(bars) < 250:
                continue
            df = bars_to_frame(bars).reset_index(drop=True)
            bh = float(df.loc[df.ts >= df.ts.iloc[-1] - pd.Timedelta(days=WINDOW_DAYS), "close"].iloc[-1] /
                       df.loc[df.ts >= df.ts.iloc[-1] - pd.Timedelta(days=WINDOW_DAYS), "open"].iloc[0] - 1.0)
            for anchor_days, step, levels, tp in product((30, 90), (0.03, 0.05), (4, 6), (0.03, 0.05)):
                ab = max(10, int(anchor_days * 24 / h))
                try:
                    m = simulate(df, ab, step, levels, tp)
                except Exception:
                    continue
                if not m:
                    continue
                rows.append({"symbol": sym, "tf": tf, "anchor_days": anchor_days,
                             "step": step, "levels": levels, "tp": tp, "bh": bh, **m})
            print(f"  {tf} {sym}", flush=True)
    d = pd.DataFrame(rows)
    d.to_csv(HERE / "grid_ladder.csv", index=False)
    print(f"\n{len(d)} runs; positive share {d.ret.gt(0).mean():.0%}")
    g = d.groupby(["tf", "anchor_days", "step", "levels", "tp"]).agg(
        n=("ret", "size"), pos=("ret", lambda s: (s > 0).mean()),
        med=("ret", "median"), mean=("ret", "mean"), best=("ret", "max"))
    print(g.sort_values(["tf", "med"], ascending=[True, False]).to_string(
        float_format=lambda v: f"{v:+.3f}"))
    best = d.sort_values("ret", ascending=False).head(12)
    print("\n=== best 12 cells ===")
    print(best[["symbol", "tf", "anchor_days", "step", "levels", "tp", "ret", "dd",
                "is_ret", "oos_ret", "bh"]].to_string(index=False, float_format=lambda v: f"{v:+.3f}"))


if __name__ == "__main__":
    main()