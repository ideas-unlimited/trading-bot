"""Cross-symbol check for a single strategy parameter set (research scratch).

Answers: is a config tuned on SOL/USDT actually the WVFX idea, or is it SOL
noise? Runs the identical causal config on several symbols over the same
two-year window and reports the IS/OOS split per symbol.

Usage:
    python research/wvfx_xcheck.py --tf 4h \
        --params "oversold=25,stop_atr=2.5,rr=2.0,max_hold=40"
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402

BARS_PER_YEAR = {"4h": 2190.0, "6h": 1460.0, "1d": 365.0}
IS_FRACTION = 0.60


def _coerce(v: str):
    if v in ("True", "False"):
        return v == "True"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def parse_params(text: str) -> dict:
    if not text.strip():
        return {}
    return {k: _coerce(v) for k, v in (kv.split("=") for kv in text.split(","))}


def run_symbol(symbol: str, tf: str, family: str, params: dict) -> dict | None:
    bars = load_scan_bars(symbol, tf, source="binance", days=730)
    if len(bars) < 400:
        print(f"! {symbol} {tf}: only {len(bars)} bars, skipped", file=sys.stderr)
        return None
    bi = int(len(bars) * IS_FRACTION)
    b_ts = bars[bi].ts
    bpy = BARS_PER_YEAR.get(tf, 365.0)
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95),
                    bars_per_year=bpy)
    eng = Engine(bars_to_frame(bars), build(family, **params), cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    is_eq = eq[eq.index < b_ts]
    oos_eq = eq[eq.index >= b_ts]
    if len(is_eq) and len(oos_eq):
        oos_eq = pd.concat([is_eq.iloc[[-1]], oos_eq])
    is_p = [t.return_pct for t in res.trades if t.entry_ts < b_ts]
    oos_p = [t.return_pct for t in res.trades if t.entry_ts >= b_ts]

    def ret(s):
        return float(s.iloc[-1] / s.iloc[0] - 1.0) if len(s) > 1 else float("nan")

    def dd(s):
        return float((s / s.cummax() - 1.0).min()) if len(s) else float("nan")

    def win(p):
        return sum(1 for x in p if x > 0) / len(p) if p else float("nan")

    allp = is_p + oos_p
    return dict(symbol=symbol, tf=tf, bars=len(bars),
                first=str(bars[0].ts.date()), last=str(bars[-1].ts.date()),
                is_ret=ret(is_eq), is_dd=dd(is_eq), is_trades=len(is_p),
                oos_ret=ret(oos_eq), oos_dd=dd(oos_eq), oos_trades=len(oos_p),
                full_ret=ret(eq), trades=len(allp), win=win(allp),
                bh=float(bars[-1].close / bars[0].close - 1.0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--family", default="wvfx_stoch")
    ap.add_argument("--params", default="")
    ap.add_argument("--symbols", default="BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT")
    args = ap.parse_args()
    params = parse_params(args.params)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"family={args.family} tf={args.tf} params={params}")
    rows = []
    for sym in symbols:
        try:
            row = run_symbol(sym, args.tf, args.family, params)
        except Exception as exc:
            print(f"! {sym}: {exc}", file=sys.stderr)
            continue
        if row:
            rows.append(row)
            msg = (f'{sym:10s} IS {row["is_ret"]:+7.1%} ({row["is_trades"]:2d} tr) | '
                   f'OOS {row["oos_ret"]:+7.1%} ({row["oos_trades"]:2d} tr) | '
                   f'full {row["full_ret"]:+7.1%} | B&H {row["bh"]:+7.1%}')
            print(msg, flush=True)
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv("research/wvfx_xcheck.csv", index=False)
    pos_oos = int((df.oos_ret > 0).sum())
    print()
    print(f"{pos_oos}/{len(df)} symbols positive OOS; median OOS {df.oos_ret.median():+.1%}; "
          f"mean OOS {df.oos_ret.mean():+.1%}")
    print(f"median full {df.full_ret.median():+.1%}; total trades {int(df.trades.sum())}")


if __name__ == "__main__":
    main()