"""Pooled WVFX parameter map on 4h across the five majors (research scratch).

Why pooled: AGENTS.md measured that per-symbol fits lose to pooled parameters
(5 of 5 coins). A single coin at 4h supplies ~9-16 trades a year, which cannot
support a multi-knob fit, so the candidate from research/wvfx_tune.py is only
interesting if it also helps the other symbols.

Every config is run causally on each symbol over the same two-year window, the
trades are split at the same 60% date, and the per-trade equity contribution
(pnl / equity at entry) is pooled across symbols. Selection uses pooled IS only.

    python research/wvfx_pooled.py --tf 4h
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402

SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT")
BARS_PER_YEAR = {"4h": 2190.0, "6h": 1460.0, "1d": 365.0}
IS_FRACTION = 0.60
MIN_IS_TRADES = 15


def contributions(bars: list, tf: str, params: dict) -> tuple[list, list, float]:
    bi = int(len(bars) * IS_FRACTION)
    b_ts = bars[bi].ts
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95),
                    bars_per_year=BARS_PER_YEAR.get(tf, 365.0))
    eng = Engine(bars_to_frame(bars), build("wvfx_stoch", **params), cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    is_tr, oos_tr = [], []
    for t in res.trades:
        try:
            base = float(eq.asof(t.entry_ts))
        except Exception:
            base = float("nan")
        if not base or math.isnan(base):
            continue
        c = t.pnl / base
        (is_tr if t.entry_ts < b_ts else oos_tr).append(c)
    full = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    return is_tr, oos_tr, full


def stats(vals: list) -> tuple[float, float]:
    if not vals:
        return float("nan"), float("nan")
    m = sum(vals) / len(vals)
    if len(vals) < 2:
        return m, float("nan")
    sd = (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5
    return m, (m / (sd / len(vals) ** 0.5)) if sd > 0 else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    # per-symbol, per-tf trade cache so the grid stays cheap
    cache = {}
    for sym in symbols:
        bars = load_scan_bars(sym, args.tf, source="binance", days=730)
        cache[sym] = bars
        print(f"{sym}: {len(bars)} bars {bars[0].ts.date()} -> {bars[-1].ts.date()}", flush=True)

    rows = []
    grid = list(itertools.product((1.5, 2.0, 2.5, 3.0), (1.5, 2.0, 2.5, 3.0),
                                  (40, 60), (100, 200), (3, 5)))
    for stop, rr, hold, regime, osw in grid:
        params = dict(oversold=20, oversold_window=osw, entry_window=1,
                      trigger="cross", regime_days=regime, stop_atr=stop,
                      rr=rr, max_hold=hold, exit_cross=False)
        is_all, oos_all, fulls = [], [], []
        per_sym_pos = 0
        for sym in symbols:
            it, ot, full = contributions(cache[sym], args.tf, params)
            is_all += it
            oos_all += ot
            fulls.append(full)
            if full > 0:
                per_sym_pos += 1
        a_is, _ = stats(is_all)
        a_oos, t_oos = stats(oos_all)
        rows.append(dict(
            stop_atr=stop, rr=rr, max_hold=hold, regime_days=regime, oversold_window=osw,
            n_is=len(is_all), avg_is=a_is, n_oos=len(oos_all), avg_oos=a_oos,
            t_oos=t_oos, med_full=float(pd.Series(fulls).median()),
            syms_pos=per_sym_pos,
            score=a_is if len(is_all) >= MIN_IS_TRADES else float("nan"),
        ))
        print(f"  stop={stop} rr={rr} hold={hold} reg={regime} osw={osw} "
              f"IS n={len(is_all):2d} {a_is:+.2%} | OOS n={len(oos_all):2d} {a_oos:+.2%}", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(f"research/wvfx_pooled_{args.tf}.csv", index=False)
    ok = df[df.score.notna()].sort_values("score", ascending=False)
    pd.set_option("display.width", 200)
    print()
    print("=== pooled, ranked by IS (selection is IS-only)")
    print(ok.head(15)[["stop_atr", "rr", "max_hold", "regime_days", "oversold_window",
                       "n_is", "avg_is", "n_oos", "avg_oos", "t_oos", "med_full",
                       "syms_pos"]].round(4).to_string(index=False))
    print()
    print("=== same table ranked by OOS (diagnostic only, never selection)")
    print(ok.sort_values("avg_oos", ascending=False).head(10)[["stop_atr", "rr", "max_hold",
          "regime_days", "oversold_window", "n_is", "avg_is", "n_oos", "avg_oos", "t_oos",
          "med_full", "syms_pos"]].round(4).to_string(index=False))
    print()
    pos_is = int((ok.avg_is > 0).sum())
    pos_oos = int((ok.avg_oos > 0).sum())
    print(f"configs {len(ok)}: {pos_is} positive IS, {pos_oos} positive OOS")


if __name__ == "__main__":
    main()