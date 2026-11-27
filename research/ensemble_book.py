"""Fixed-sleeve per-symbol books (no per-symbol parameter search).

The per-symbol study showed that choosing the best config on two years of
data does not survive the next year.  This script tests the alternative:
give every symbol the same small set of fixed sleeves and let the symbol's
own price action decide which sleeve is in the market.

Sleeves are independent $ wallets; the combined equity is their sum, so
there is no leverage and no rebalancing.  Costs are zero per the brief.

Run:  .venv\\Scripts\\python.exe -u research/ensemble_book.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bot.backtest import Engine  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.strategy import build  # noqa: E402
from per_symbol import SYMS, TRAIN_END, load  # noqa: E402

BASE = {
    "wvf": ("wvfx_stoch", dict(trigger="cross", rr=2.0, stop_atr=2.0, max_hold=40)),
    "sma": ("sma_cross", dict(fast=30, slow=50, atr_mult=2.0)),
    "mom": ("momentum_swing", dict(lookback=40, atr_mult=2.5, trail_atr=3.0)),
}


def run(df: pd.DataFrame, family: str, params: dict, cash: float):
    cfg = BotConfig(
        initial_cash=cash,
        costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),
        risk=RiskConfig(risk_per_trade=0.02, max_position_pct=0.95, max_drawdown_stop=0.50),
        bars_per_year=2190.0,
    )
    engine = Engine(df, build(family, **params), cfg)
    engine.run()
    return engine.result()


def stats(eq: pd.Series) -> tuple[float, float, float]:
    eq = eq.astype(float)
    r = eq.pct_change().dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(2190.0)) if r.std(ddof=1) > 0 else float("nan")
    return total, dd, sharpe


def portfolio(keys: list[str]) -> pd.Series:
    per = 1000.0 / len(SYMS) / len(keys)
    total: pd.Series | None = None
    for sym in SYMS:
        df = load(sym)
        for key in keys:
            family, params = BASE[key]
            eq = run(df, family, params, per).equity_curve
            total = eq if total is None else total + eq
    assert total is not None
    return total


def main() -> None:
    print("$1,000 total, equal cash per symbol x sleeve, 2% risk per sleeve, zero fees")
    print("full 2023-11-26..2026-11-26; OOS 2025-11-26..2026-11-26; 4h Binance")
    for label, keys in [
        ("wvf only", ["wvf"]),
        ("wvf+sma", ["wvf", "sma"]),
        ("3-sleeve", ["wvf", "sma", "mom"]),
    ]:
        eq = portfolio(keys)
        full = stats(eq)
        oos = stats(eq[eq.index >= TRAIN_END])
        print(
            f"{label:9s} full {full[0] * 100:+7.1f}% dd {full[1] * 100:+6.1f}% sh {full[2]:4.2f}"
            f" | OOS {oos[0] * 100:+7.1f}% dd {oos[1] * 100:+6.1f}% sh {oos[2]:4.2f}"
        )

    print()
    print("per-symbol sleeve OOS (each symbol treated as its own $1,000 book)")
    for sym in SYMS:
        df = load(sym)
        cells = []
        for label, keys in [("wvf+sma", ["wvf", "sma"]), ("3-sleeve", ["wvf", "sma", "mom"])]:
            eq = None
            for key in keys:
                family, params = BASE[key]
                one = run(df, family, params, 1000.0 / len(keys)).equity_curve
                eq = one if eq is None else eq + one
            assert eq is not None
            ret, dd, _ = stats(eq[eq.index >= TRAIN_END])
            cells.append(f"{label} {ret * 100:+6.1f}% (dd {dd * 100:+5.1f}%)")
        print(f"  {sym:8s} " + " | ".join(cells))


if __name__ == "__main__":
    main()