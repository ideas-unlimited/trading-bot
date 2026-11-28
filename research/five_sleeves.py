"""Per-coin tuning of the five shortlisted sleeves (one year, 4h chart).

The all-coin sweep ranked 1,534 cells; this takes the five coin/rule pairs it
put on top and tunes each rule ON ITS OWN COIN, then reports the standard
analysis table: return, max drawdown, Sharpe, Sortino, Calmar, win rate with
its confidence interval, profit factor, expectancy in R and the probability
that a year like this repeats.

Protocol: signal on the close, fill next open, 10 bps fee + 5 bps slippage a
side, 3% risk, $1,000, long only, 365 measured days (455 loaded so warmup
burns outside the window), account drawdown switch off.

Selection: full-year Calmar = return / |max drawdown|, among cells with at
least 12 trades and a drawdown no worse than -35%. That IS fitting on the
reported year, so every pick also carries:
  * the year cut in half (H1 / H2 return and Calmar),
  * the neighbourhood median: cells one grid step away,
  * a 5,000-draw trade-order bootstrap at 3% risk -> P(positive year),
    median / 5th-percentile return, median / 5th-percentile drawdown.

    python research/five_sleeves.py            # grid + report
    python research/five_sleeves.py --report   # report the saved CSV only
"""

from __future__ import annotations

import itertools
import json
import math
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE / "cake"))

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402

try:
    from cake_swing import SwingLab  # noqa: E402
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"cannot import SwingLab: {exc}")

TF = "4h"
BAR_HOURS = 4.0
BPY = 365.0 * 24.0 / BAR_HOURS
WINDOW_DAYS = 365
LOAD_DAYS = 455
RISK = 0.03
MIN_TRADES = 12
DD_FLOOR = -0.35
BOOT = 5000
WORKERS = 8

TARGETS = [
    ("NEAR/USDT", "pullback"),
    ("RENDER/USDT", "breakout"),
    ("ARB/USDT", "breakout"),
    ("PYTH/USDT", "pullback"),
    ("JUP/USDT", "capdip"),
]


def axes_for(kind: str) -> dict:
    if kind == "pullback":
        return {"fast_hours": [12, 24, 48], "trend_hours": [480, 1200],
                "stop_atr": [1.5, 2.0, 3.0], "rr": [1.5, 2.0, 2.5, 3.0],
                "max_hold_hours": [72, 120]}
    if kind == "breakout":
        return {"lookback_hours": [72, 120, 168, 240], "trend_hours": [480, 1200],
                "stop_atr": [1.5, 2.0, 2.5, 3.0],
                "exit": [("trail", 2.0), ("trail", 3.0), ("trail", 4.0), ("trail", 5.0),
                         ("rr", 2.0), ("rr", 3.0), ("rr", 4.0)],
                "max_hold_hours": [72]}
    return {"window_hours": [120, 240, 360], "drop_pct": [0.15, 0.20, 0.25, 0.30],
            "stop_atr": [2.0, 2.5, 3.0], "rr": [1.5, 2.0, 2.5],
            "max_hold_hours": [120, 240]}


def b(hours: float) -> int:
    return max(1, int(round(hours / BAR_HOURS)))


def label_of(kind: str, kw: dict) -> str:
    if kind == "pullback":
        return (f"pullback f{kw['fast_hours']:.0f}/t{kw['trend_hours']:.0f} "
                f"s{kw['stop_atr']:g} rr{kw['rr']:g} h{kw['max_hold_hours']:.0f}")
    if kind == "breakout":
        mode, val = kw["exit"]
        exit_txt = f"trail{val:g}" if mode == "trail" else f"rr{val:g}"
        return (f"breakout {kw['lookback_hours']:.0f}h t{kw['trend_hours']:.0f} "
                f"s{kw['stop_atr']:g} {exit_txt} h{kw['max_hold_hours']:.0f}")
    return (f"capdip {kw['window_hours']:.0f}h d{kw['drop_pct']:.2f} "
            f"s{kw['stop_atr']:g} rr{kw['rr']:g} h{kw['max_hold_hours']:.0f}")


def make(kind: str, kw: dict):
    if kind == "capdip":
        return build("capitulation_dip_uni", window=b(kw["window_hours"]),
                     drop_pct=kw["drop_pct"], stop_atr=kw["stop_atr"], rr=kw["rr"],
                     max_hold=b(kw["max_hold_hours"]))
    if kind == "breakout":
        mode, val = kw["exit"]
        return SwingLab(family="breakout", lookback_hours=kw["lookback_hours"],
                        trend_hours=kw["trend_hours"], stop_atr=kw["stop_atr"],
                        rr=None if mode == "trail" else val,
                        trail_atr=val if mode == "trail" else None,
                        max_hold_hours=kw["max_hold_hours"])
    return SwingLab(family="pullback", fast_hours=kw["fast_hours"],
                    trend_hours=kw["trend_hours"], stop_atr=kw["stop_atr"],
                    rr=kw["rr"], max_hold_hours=kw["max_hold_hours"])


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    z = 1.96
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _sharpe(daily: pd.Series) -> float:
    r = daily.pct_change().dropna()
    if len(r) < 5:
        return float("nan")
    sd = float(r.std(ddof=1))
    if not sd or not np.isfinite(sd):
        return float("nan")
    return float(r.mean() / sd * math.sqrt(365.0))


def _sortino(daily: pd.Series) -> float:
    r = daily.pct_change().dropna()
    if len(r) < 5:
        return float("nan")
    dn = float(np.sqrt(np.mean(np.minimum(r.to_numpy(), 0.0) ** 2)))
    if not dn:
        return float("nan")
    return float(r.mean() / dn * math.sqrt(365.0))


def _calmar(ret: float, dd: float) -> float:
    if not np.isfinite(dd) or dd >= 0:
        return float("nan")
    return float(ret / abs(dd))


def analyse(res, start_ts) -> dict | None:
    eq = res.equity_curve.astype(float)
    eqw = eq[eq.index >= start_ts]
    if len(eqw) < 10:
        return None
    base = float(eqw.iloc[0])
    end = float(eqw.iloc[-1])
    ret = end / base - 1.0
    peak = eqw.cummax()
    dd_series = eqw / peak - 1.0
    dd = float(dd_series.min())
    daily = eqw.resample("1D").last().dropna()
    tr = [t for t in res.trades if t.entry_ts >= start_ts]
    pnls = [float(t.pnl) for t in tr]
    rets = [float(t.return_pct) for t in tr]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(tr)
    wr = (len(wins) / n) if n else float("nan")
    lo, hi = _wilson(len(wins), n)
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf") if wins else float("nan")
    rs = []
    for t in tr:
        at_entry = eqw.asof(pd.Timestamp(t.entry_ts))
        risk_amt = RISK * float(at_entry)
        if risk_amt > 0 and np.isfinite(risk_amt):
            rs.append(float(t.pnl) / risk_amt)
    consec = cur = 0
    for p in pnls:
        cur = cur + 1 if p <= 0 else 0
        consec = max(consec, cur)
    mid = len(eqw) // 2
    h1_ret = float(eqw.iloc[mid] / base - 1.0)
    h2_ret = float(end / eqw.iloc[mid] - 1.0)
    h1_dd = float(dd_series.iloc[:mid].min()) if mid > 1 else float("nan")
    h2_dd = float(dd_series.iloc[mid:].min()) if mid < len(eqw) else float("nan")
    return {
        "trades": n,
        "win": wr, "win_lo": lo, "win_hi": hi,
        "avg_win": float(np.mean([r for r in rets if r > 0])) if wins else float("nan"),
        "avg_loss": float(np.mean([r for r in rets if r <= 0])) if losses else float("nan"),
        "pf": float(pf),
        "exp_r": float(np.mean(rs)) if rs else float("nan"),
        "ret": float(ret),
        "dd": dd,
        "sharpe": _sharpe(daily),
        "sortino": _sortino(daily),
        "calmar": _calmar(ret, dd),
        "expo": float(res.exposure[res.exposure.index >= start_ts].mean()),
        "h1_ret": h1_ret, "h2_ret": h2_ret,
        "h1_calmar": _calmar(h1_ret, h1_dd),
        "h2_calmar": _calmar(h2_ret, h2_dd),
        "max_cl": int(consec),
        "best": float(max(rets)) if rets else float("nan"),
        "worst": float(min(rets)) if rets else float("nan"),
        "final_eq": end,
        "rs": rs,
    }


def bootstrap(rs: list[float], iters: int = BOOT, seed: int = 7) -> dict:
    if len(rs) < 8:
        return {"p_pos": float("nan"), "med": float("nan"), "p05": float("nan"),
                "p95": float("nan"), "med_dd": float("nan"), "p05_dd": float("nan")}
    arr = np.asarray(rs, dtype=float)
    rng = np.random.default_rng(seed)
    finals = np.empty(iters)
    dds = np.empty(iters)
    for k in range(iters):
        s = arr[rng.integers(0, len(arr), len(arr))]
        eq = np.cumprod(1.0 + RISK * s)
        peak = np.maximum.accumulate(eq)
        dds[k] = float((eq / peak - 1.0).min())
        finals[k] = float(eq[-1] - 1.0)
    return {
        "p_pos": float((finals > 0).mean()),
        "med": float(np.median(finals)),
        "p05": float(np.percentile(finals, 5)),
        "p95": float(np.percentile(finals, 95)),
        "med_dd": float(np.median(dds)),
        "p05_dd": float(np.percentile(dds, 5)),
    }


def cfg() -> BotConfig:
    return BotConfig(initial_cash=1000.0, costs=CostConfig(),
                     risk=RiskConfig(risk_per_trade=RISK, max_position_pct=0.95,
                                     max_drawdown_stop=0.99),
                     bars_per_year=BPY)


def grid_rows(task: tuple[str, str]) -> list[dict]:
    sym, kind = task
    bars = load_scan_bars(sym, TF, source="binance", days=LOAD_DAYS)
    df = bars_to_frame(bars)
    start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    ax = axes_for(kind)
    keys = list(ax)
    vals = [ax[k] for k in keys]
    rows: list[dict] = []
    for combo in itertools.product(*vals):
        kw = dict(zip(keys, combo))
        strat = make(kind, kw)
        eng = Engine(df, strat, cfg())
        eng.run()
        m = analyse(eng.result(), start_ts)
        if m is None:
            continue
        idx = ",".join(str(vals[i].index(combo[i])) for i in range(len(keys)))
        m.update(symbol=sym, kind=kind, label=label_of(kind, kw), grid=idx)
        m["params"] = ";".join(f"{k}={v}" for k, v in kw.items())
        rows.append(m)
    return rows


def neighbours(rows: list[dict], best: dict) -> float:
    bi = [int(v) for v in best["grid"].split(",")]
    near = []
    for r in rows:
        ri = [int(v) for v in r["grid"].split(",")]
        if ri == bi:
            continue
        if all(abs(a - b_) <= 1 for a, b_ in zip(ri, bi)):
            near.append(r["ret"])
    return float(np.median(near)) if near else float("nan")


def select(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["trades"] >= MIN_TRADES and r["dd"] >= DD_FLOOR
          and np.isfinite(r["calmar"])]
    if not ok:
        ok = [r for r in rows if r["trades"] >= MIN_TRADES] or rows
    return max(ok, key=lambda r: r["calmar"])


PCT = ("ret", "dd", "win", "h1_ret", "h2_ret", "expo", "avg_win", "avg_loss",
       "best", "worst", "nb_med", "p_pos", "boot_med", "boot_p05", "boot_p95",
       "boot_med_dd", "boot_p05_dd", "best_ret_alt", "best_ret_alt_dd")
NUM = ("sharpe", "sortino", "calmar", "pf", "exp_r", "h1_calmar", "h2_calmar")


def show(d: pd.DataFrame, cols: list[str]) -> str:
    out = d.reindex(columns=cols).copy()
    for c in cols:
        if c in PCT:
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v * 100:+.1f}%")
        elif c in ("win_lo", "win_hi"):
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v * 100:.0f}%")
        elif c in NUM:
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
    return out.to_string(index=False)


def run_grid() -> None:
    with mp.Pool(WORKERS) as pool:
        results = pool.map(grid_rows, TARGETS)
    allrows: list[dict] = []
    store: dict[tuple[str, str], list[float]] = {}
    for (sym, kind), rows in zip(TARGETS, results):
        for r in rows:
            store[(sym, r["label"])] = r.pop("rs")
        allrows.extend(rows)
        good = [r for r in rows if r["trades"] >= MIN_TRADES]
        print(f"  {sym:12s} {kind:9s} {len(rows):4d} cells, "
              f"{len(good):4d} with {MIN_TRADES}+ trades", flush=True)
    d = pd.DataFrame(allrows)
    d.to_csv(HERE / "five_sleeves.csv", index=False)
    picks = []
    for sym, kind in TARGETS:
        rows = [r for r in allrows if r["symbol"] == sym]
        best = select(rows)
        boot = bootstrap(store[(sym, best["label"])])
        nb = neighbours(rows, best)
        raw = [r for r in rows if r["trades"] >= MIN_TRADES]
        top = max(raw, key=lambda r: r["ret"])
        pretty = {k: v for k, v in best.items() if k not in ("rs", "grid", "params")}
        pretty.update({f"boot_{k}": v for k, v in boot.items() if k != "p_pos"})
        pretty["p_pos"] = boot["p_pos"]
        pretty["boot_p05_dd"] = boot["p05_dd"]
        pretty["boot_p05"] = boot["p05"]
        pretty["nb_med"] = nb
        pretty["kind"] = kind
        pretty["best_ret_alt"] = top["ret"]
        pretty["best_ret_alt_dd"] = top["dd"]
        pretty["best_ret_alt_label"] = top["label"]
        pretty["cells"] = len(rows)
        pretty["cells_ok"] = len(raw)
        picks.append(pretty)
        print(f"\n=== {sym} ({kind}), {len(rows)} cells, {len(raw)} with {MIN_TRADES}+ trades ===")
        print(show(pd.DataFrame([pretty]), ["symbol", "label", "trades", "win", "exp_r",
                                            "pf", "ret", "dd", "sharpe", "calmar",
                                            "h1_ret", "h2_ret", "nb_med", "p_pos",
                                            "boot_med", "boot_p05", "boot_p05_dd"]))
        print(f"  best raw return: {top['label']}  {top['ret'] * 100:+.1f}%  "
              f"dd {top['dd'] * 100:+.1f}%")
        t = pd.DataFrame(sorted(raw, key=lambda r: -r["calmar"])[:8])
        print(show(t, ["label", "trades", "win", "ret", "dd", "sharpe", "calmar",
                       "h1_ret", "h2_ret"]))
    p = pd.DataFrame(picks)
    p.to_csv(HERE / "five_sleeves_picks.csv", index=False)
    print(f"\nwrote {HERE / 'five_sleeves.csv'} ({len(d)} cells) and "
          f"{HERE / 'five_sleeves_picks.csv'} ({len(p)} picks)")


def report() -> None:
    p = pd.read_csv(HERE / "five_sleeves_picks.csv")
    print(show(p, ["symbol", "label", "trades", "win", "win_lo", "win_hi", "exp_r",
                   "pf", "ret", "dd", "sharpe", "sortino", "calmar", "expo",
                   "h1_ret", "h2_ret", "max_cl", "best", "worst", "nb_med",
                   "p_pos", "boot_med", "boot_p05", "boot_med_dd", "boot_p05_dd"]))


def main() -> None:
    if "--report" in sys.argv:
        report()
    else:
        run_grid()


if __name__ == "__main__":
    main()
