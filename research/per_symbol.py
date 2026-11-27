"""Per-symbol walk-forward selection across the current strategy set.

For every symbol, every candidate config is run once over the three-year 4h
sample.  Configs are ranked on the *first two years* (expectancy per trade,
minimum trade count, drawdown cap) and then scored on the untouched final
year.  This is deliberately not an in-sample beauty contest: the selected
config table is the honest out-of-sample result.

Run:  .venv\\Scripts\\python.exe -u research/per_symbol.py
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bot.backtest import BacktestResult, Engine  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.metrics import compute_metrics  # noqa: E402
from bot.strategy import build  # noqa: E402

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
MID = pd.Timestamp("2024-11-26")
TRAIN_END = pd.Timestamp("2025-11-26")
CASH = 1000.0
RISK = 0.02
BPY = 2190.0  # 4h bars/year
MIN_TRAIN_TRADES = 6
MAX_TRAIN_DD = -0.30


@dataclass
class Row:
    sym: str
    family: str
    short: str
    label: str
    params: dict
    train: dict
    test: dict
    h1: dict
    h2: dict


def load(sym: str) -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / "crypto" / f"{sym}_4h.csv")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.drop(columns=["timestamp"]).sort_values("ts").reset_index(drop=True)
    cutoff = df["ts"].max() - pd.DateOffset(years=3)
    return df[df["ts"] >= cutoff].reset_index(drop=True)


def configs() -> list[tuple[str, str, str, dict]]:
    out: list[tuple[str, str, str, dict]] = []

    for trig in ("cross", "state"):
        for rr in (1.5, 2.0, 2.5):
            for stop, hold in ((2.0, 40), (2.5, 50)):
                out.append((
                    "wvfx_stoch", "wvf",
                    f"wvf {trig} rr{rr:g} stop{stop:g} h{hold}",
                    dict(trigger=trig, rr=rr, stop_atr=stop, max_hold=hold),
                ))

    for lb in (40, 60, 90):
        for atr in (2.0, 2.5):
            for trail in (3.0, 3.5):
                out.append((
                    "momentum_swing", "mom",
                    f"mom lb{lb} atr{atr:g} tr{trail:g}",
                    dict(lookback=lb, atr_mult=atr, trail_atr=trail),
                ))

    for entry, exit_ in ((20, 10), (55, 10), (55, 20), (100, 20)):
        for atr in (2.0, 3.0):
            out.append((
                "donchian", "don",
                f"don {entry}/{exit_} atr{atr:g}",
                dict(entry_window=entry, exit_window=exit_, atr_mult=atr),
            ))

    for entry in (25, 30, 35):
        for exit_level in (55, 65):
            out.append((
                "rsi_meanrev", "rsi",
                f"rsi {entry}/{exit_level} trend200",
                dict(entry=entry, exit_level=exit_level, trend_window=200),
            ))

    for vz in (1.5, 2.0, 2.5):
        for drop in (0.04, 0.06, 0.08):
            out.append((
                "vol_climax", "vol",
                f"vol z{vz:g} drop{int(drop * 100)}% rr2",
                dict(vol_z=vz, drop_pct=drop, rr=2.0),
            ))

    for fast, slow in ((20, 50), (30, 50), (50, 100), (20, 100)):
        for atr in (2.0, 2.5):
            out.append((
                "sma_cross", "sma",
                f"sma {fast}/{slow} atr{atr:g}",
                dict(fast=fast, slow=slow, atr_mult=atr),
            ))

    return out


DEFAULTS = {
    "wvfx_stoch": "wvf cross rr2 stop2 h40",
    "momentum_swing": "mom lb60 atr2.5 tr3.5",
    "donchian": "don 55/10 atr3",
    "rsi_meanrev": "rsi 30/55 trend200",
    "volume_climax": "vol z2 drop6% rr2",
    "sma_cross": "sma 20/50 atr2.5",
}


def run(df: pd.DataFrame, family: str, params: dict) -> BacktestResult:
    strat = build(family, **params)
    cfg = BotConfig(
        initial_cash=CASH,
        costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),
        risk=RiskConfig(risk_per_trade=RISK, max_position_pct=0.95, max_drawdown_stop=0.50),
        bars_per_year=BPY,
    )
    eng = Engine(df, strat, cfg)
    eng.run()
    return eng.result()


def segment(result: BacktestResult, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    eq = result.equity_curve
    mask = (eq.index >= start) & (eq.index < end)
    e = eq.loc[mask]
    if len(e) < 2:
        return {}
    trades = [t for t in result.trades if t.entry_ts >= start and t.exit_ts <= end]
    sliced = BacktestResult(
        strategy=result.strategy,
        initial_equity=float(e.iloc[0]),
        equity_curve=e,
        close=result.close.loc[e.index],
        trades=trades,
        fills=[],
        exposure=result.exposure.loc[e.index],
    )
    return compute_metrics(sliced, bars_per_year=BPY)


def pick_naive(rows: list[Row]) -> Row | None:
    """Old selector: maximise train expectancy, only a trade-count floor."""
    ok: list[Row] = []
    for r in rows:
        t = r.train
        if not t or t.get("trades", 0) < MIN_TRAIN_TRADES:
            continue
        avg = t.get("avg_trade", float("nan"))
        if not math.isfinite(avg):
            continue
        if t.get("max_drawdown", -1.0) <= MAX_TRAIN_DD:
            continue
        ok.append(r)
    if not ok:
        return None
    return max(ok, key=lambda r: (r.train["avg_trade"], r.train.get("sharpe", -9.0)))


def pick_robust(rows: list[Row]) -> Row | None:
    """Require both train years to work, a real win rate and a sane drawdown."""
    ok: list[Row] = []
    for r in rows:
        t, a, b = r.train, r.h1, r.h2
        if not t or not a or not b:
            continue
        if t.get("trades", 0) < MIN_TRAIN_TRADES:
            continue
        if a.get("trades", 0) < 3 or b.get("trades", 0) < 3:
            continue
        if a.get("total_return", -1.0) <= 0 or b.get("total_return", -1.0) <= 0:
            continue
        if t.get("win_rate", 0.0) < 0.30:
            continue
        if t.get("max_drawdown", -1.0) <= MAX_TRAIN_DD:
            continue
        if t.get("profit_factor", 0.0) < 1.20:
            continue
        score = min(a.get("sharpe", -9.0), b.get("sharpe", -9.0))
        if not math.isfinite(score):
            continue
        ok.append((score, r))
    if not ok:
        return None
    return max(ok, key=lambda x: (x[0], x[1].train.get("sharpe", -9.0)))[1]


def robust_score(r: Row) -> float:
    return min(r.h1.get("sharpe", -9.0), r.h2.get("sharpe", -9.0))


def ret(m: dict) -> float:
    return float(m.get("total_return", 0.0)) if m else 0.0


def fmt(m: dict) -> str:
    if not m:
        return "n/a"
    return (
        f"n={int(m['trades']):3d} ret={m['total_return'] * 100:+7.1f}% "
        f"dd={m['max_drawdown'] * 100:+6.1f}% sh={m['sharpe']:5.2f} "
        f"win={m['win_rate'] * 100:5.1f}% avg={m['avg_trade'] * 100:+5.2f}%"
    )


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def main() -> None:
    cfg_list = configs()
    print(f"configs={len(cfg_list)} symbols={len(SYMS)} runs={len(cfg_list) * len(SYMS)}")

    data = {s: load(s) for s in SYMS}
    end = max(df["ts"].iloc[-1] for df in data.values()) + pd.Timedelta(hours=4)
    all_rows: list[Row] = []

    for sym, df in data.items():
        start = df["ts"].iloc[0]
        print(f"  running {sym} ...", flush=True)
        for family, short, label, params in cfg_list:
            res = run(df, family, params)
            all_rows.append(Row(
                sym=sym, family=family, short=short, label=label, params=params,
                train=segment(res, start, TRAIN_END),
                test=segment(res, TRAIN_END, end),
                h1=segment(res, start, MID),
                h2=segment(res, MID, TRAIN_END),
            ))

    print()
    print("=== robust per-symbol selection: trained 2023-11-26..2025-11-26, tested 2025-11-26..2026-11-26 ===")
    print("    (family chosen first; config must work in BOTH train years, win>=30%, PF>=1.2)")
    fams = [(f, s) for f, s in dict.fromkeys((r.family, r.short) for r in all_rows)]
    selected: dict[str, Row] = {}
    defaults: dict[str, Row] = {}
    naive_sel: dict[str, Row] = {}
    for sym in SYMS:
        rows = [r for r in all_rows if r.sym == sym]
        default = next(r for r in rows if r.label == DEFAULTS["wvfx_stoch"])
        defaults[sym] = default

        fam_candidates: list[tuple[float, Row]] = []
        for family, _ in fams:
            best_fam = pick_robust([r for r in rows if r.family == family])
            if best_fam is not None:
                fam_candidates.append((robust_score(best_fam), best_fam))
        fallback = not fam_candidates
        if fallback:
            best = default
        else:
            best = max(fam_candidates, key=lambda x: (x[0], x[1].train.get("sharpe", -9.0)))[1]
        selected[sym] = best
        naive = pick_naive(rows)
        naive_sel[sym] = naive if naive is not None else default

        print(f"{sym:8s} {best.short:4s} {best.label:30s}" + ("   [fallback]" if fallback else ""))
        print(f"  train {fmt(best.train)}")
        print(f"  H1    {fmt(best.h1)}")
        print(f"  H2    {fmt(best.h2)}")
        print(f"  OOS   {fmt(best.test)}   default-wvf {ret(default.test) * 100:+6.1f}%")

    sel = [ret(selected[s].test) for s in SYMS]
    dflt = [ret(defaults[s].test) for s in SYMS]
    naive = [ret(naive_sel[s].test) for s in SYMS]
    print()
    print(f"mean robust per-symbol OOS   {mean(sel) * 100:+6.2f}%   median {pd.Series(sel).median() * 100:+6.2f}%   positive {sum(x > 0 for x in sel)}/{len(sel)}")
    print(f"mean naive per-symbol OOS    {mean(naive) * 100:+6.2f}%   median {pd.Series(naive).median() * 100:+6.2f}%   positive {sum(x > 0 for x in naive)}/{len(naive)}")
    print(f"mean default-wvf OOS         {mean(dflt) * 100:+6.2f}%   median {pd.Series(dflt).median() * 100:+6.2f}%")
    print(f"robust edge vs default-wvf   {(mean(sel) - mean(dflt)) * 100:+6.2f} pp")

    # One config for every symbol: rank by mean train expectancy, require
    # coverage on at least three symbols.
    by_label: dict[tuple[str, str], list[Row]] = {}
    for r in all_rows:
        by_label.setdefault((r.family, r.label), []).append(r)
    global_ranked = []
    for key, rows in by_label.items():
        valid = [r for r in rows if r.train and r.train.get("trades", 0) >= MIN_TRAIN_TRADES
                 and math.isfinite(r.train.get("avg_trade", float("nan")))]
        if len(valid) >= 3:
            global_ranked.append((mean([r.train["avg_trade"] for r in valid]), key, rows))
    if global_ranked:
        _, key, rows = max(global_ranked, key=lambda x: x[0])
        gtest = [ret(r.test) for r in rows]
        print()
        print(f"best single global config: {key[1]}")
        print(f"  mean train expectancy {mean([r.train['avg_trade'] for r in rows if r.train]) * 100:+.2f}% per trade")
        print(f"  mean OOS return {mean(gtest) * 100:+6.2f}%   median {pd.Series(gtest).median() * 100:+6.2f}%   positive {sum(x > 0 for x in gtest)}/{len(gtest)}")

    print()
    print("=== best robust config per family (picked on train, OOS return / trade count) ===")
    print("sym      " + "".join(f"{short:>13s}" for _, short in fams))
    for sym in SYMS:
        cells = []
        for family, _ in fams:
            rows = [r for r in all_rows if r.sym == sym and r.family == family]
            best = pick_robust(rows)
            if best is None:
                cells.append(f"{'-':>13s}")
            else:
                cells.append(f"{ret(best.test) * 100:+7.1f}% ({int(best.test.get('trades', 0)):2d})")
        print(f"{sym:8s} " + "".join(f"{c:>13s}" for c in cells))

    out = ROOT / "runs" / "per_symbol_4h.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "sym": r.sym, "family": r.family, "label": r.label,
            "train_n": r.train.get("trades"), "train_ret": r.train.get("total_return"),
            "train_dd": r.train.get("max_drawdown"), "train_sh": r.train.get("sharpe"),
            "train_avg": r.train.get("avg_trade"), "train_win": r.train.get("win_rate"),
            "train_pf": r.train.get("profit_factor"),
            "h1_ret": r.h1.get("total_return"), "h2_ret": r.h2.get("total_return"),
            "h1_sh": r.h1.get("sharpe"), "h2_sh": r.h2.get("sharpe"),
            "test_n": r.test.get("trades"), "test_ret": r.test.get("total_return"),
            "test_dd": r.test.get("max_drawdown"), "test_sh": r.test.get("sharpe"),
            "test_win": r.test.get("win_rate"),
        }
        for r in all_rows
    ]).to_csv(out, index=False)
    print(f"\nrows -> {out}")


if __name__ == "__main__":
    main()