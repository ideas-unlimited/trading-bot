"""WVFX-Stoch tuning lab for SOL/USDT (research scratch, 2026-11-28).

Goal: fine-tune `wvfx_stoch` on 4h / 6h / 1d and report honestly whether the
setup fits any of them. Protocol follows AGENTS.md and research/PER_SYMBOL.md:

* one causal run per config over the full 2-year window, then split the
  resulting equity curve / trade list into in-sample (first 60%) and
  untouched out-of-sample (last 40%);
* SELECT on IS only, using the worse of two IS halves so a config must work
  in both halves, never on OOS;
* rank plateaus, not single cells (the PER_SYMBOL failure mode);
* full costs (10 bps fee + 5 bps slippage), $1,000, 3% risk, long-only spot.

Stages:
  1  entry rarity grid (oversold x window x entry_window x trigger x regime)
  2  exit grid on the best entries (stop x rr x hold x exit_cross)
  3  WVF / stochastic indicator params around the stage-2 winner
  4  optional trailing-stop variant (lab subclass, no core code change)

Usage:
    python research/wvfx_tune.py --stage 1
    python research/wvfx_tune.py --all
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys
from dataclasses import replace

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from bot.strategy.base import Action  # noqa: E402
from bot.strategy.wvfx_stoch import WilliamVixFixStoch  # noqa: E402

SYMBOL = "SOL/USDT"
INITIAL_CASH = 1_000.0
RISK = 0.03
IS_FRACTION = 0.60
MIN_IS_TRADES = 5
BARS_PER_YEAR = {"4h": 2190.0, "6h": 1460.0, "1d": 365.0}


class WvfxTrail(WilliamVixFixStoch):
    """WVFX with an optional Chandelier trail (lab probe, not promoted)."""

    name = "wvfx_trail"
    PARAMS = WilliamVixFixStoch.PARAMS + ("trail_atr", "keep_target")

    def __init__(self, trail_atr: float = 3.0, keep_target: bool = True, **kw) -> None:
        self.trail_atr = trail_atr
        self.keep_target = keep_target
        super().__init__(**kw)

    def signal(self, i: int, df: pd.DataFrame, position: float):
        sig = super().signal(i, df, position)
        if sig is None or sig.action is not Action.BUY:
            return sig
        return replace(
            sig,
            trail_atr=self.trail_atr,
            take_profit=sig.take_profit if self.keep_target else None,
        )


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def _metrics(eq: pd.Series, pnls: list[float], bpy: float) -> dict:
    out = dict(ret=float("nan"), dd=float("nan"), sharpe=float("nan"),
               trades=0, win=float("nan"), pf=float("nan"))
    if len(eq) < 2:
        return out
    eq = eq.astype(float)
    out["ret"] = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    out["dd"] = float((eq / eq.cummax() - 1.0).min())
    rets = eq.pct_change().dropna()
    sd = rets.std(ddof=1)
    if len(rets) > 1 and sd and sd > 0:
        out["sharpe"] = float(rets.mean() / sd * math.sqrt(bpy))
    out["trades"] = len(pnls)
    if pnls:
        wins = [p for p in pnls if p > 0]
        gl = abs(sum(p for p in pnls if p < 0))
        out["win"] = len(wins) / len(pnls)
        out["pf"] = (sum(wins) / gl) if gl > 0 else float("inf")
    return out


def run_one(strat, bars: list, bpy: float, boundary_i: int) -> dict:
    """One causal run; split equity and trades into slices afterwards."""
    df = bars_to_frame(bars)
    cfg = BotConfig(
        initial_cash=INITIAL_CASH,
        costs=CostConfig(),
        risk=RiskConfig(risk_per_trade=RISK, max_position_pct=0.95),
        bars_per_year=bpy,
    )
    eng = Engine(df, strat, cfg)
    eng.run()
    res = eng.result()

    eq = res.equity_curve
    b_ts = bars[boundary_i].ts
    mid_i = boundary_i // 2
    m_ts = bars[mid_i].ts

    def span(lo, hi):
        return eq[(eq.index >= lo) & (eq.index < hi)]

    is_eq = span(eq.index[0], b_ts)
    h1_eq = span(eq.index[0], m_ts)
    h2_eq = span(m_ts, b_ts)
    oos_eq = span(b_ts, eq.index[-1] + pd.Timedelta(seconds=1))
    if len(is_eq) and len(oos_eq):
        oos_eq = pd.concat([is_eq.iloc[[-1]], oos_eq])

    def pnls_between(lo, hi):
        return [t.return_pct for t in res.trades if lo <= t.entry_ts < hi]

    is_p = pnls_between(bars[0].ts, b_ts)
    h1_p = pnls_between(bars[0].ts, m_ts)
    h2_p = pnls_between(m_ts, b_ts)
    oos_p = pnls_between(b_ts, bars[-1].ts + pd.Timedelta(days=1))

    m_is = _metrics(is_eq, is_p, bpy)
    m_oos = _metrics(oos_eq, oos_p, bpy)
    m_h1 = _metrics(h1_eq, h1_p, bpy)
    m_h2 = _metrics(h2_eq, h2_p, bpy)
    row = {"is_ret": m_is["ret"], "is_dd": m_is["dd"], "is_sharpe": m_is["sharpe"],
           "is_trades": m_is["trades"], "is_win": m_is["win"], "is_pf": m_is["pf"],
           "h1_ret": m_h1["ret"], "h2_ret": m_h2["ret"],
           "oos_ret": m_oos["ret"], "oos_dd": m_oos["dd"], "oos_sharpe": m_oos["sharpe"],
           "oos_trades": m_oos["trades"], "oos_win": m_oos["win"], "oos_pf": m_oos["pf"],
           "full_ret": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
           "halted": res.halted_reason or ""}
    # selection score: worse IS half (both halves must pay), NaN if too few trades
    if m_is["trades"] >= MIN_IS_TRADES and not (math.isnan(m_h1["ret"]) or math.isnan(m_h2["ret"])):
        row["score"] = min(m_h1["ret"], m_h2["ret"])
    else:
        row["score"] = float("nan")
    return row


def _fmt(df: pd.DataFrame, cols: list[str]) -> str:
    d = df.copy()
    for c in cols:
        if c in d.columns and d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    return d.to_string(index=False)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage1(tfs):  # entry rarity
    rows = []
    for tf in tfs:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
        bpy = BARS_PER_YEAR.get(tf, 365.0)
        bi = int(len(bars) * IS_FRACTION)
        grid = itertools.product((20, 25, 30, 35), (3, 5, 8), (1, 3),
                                 ("cross", "state"), (0, 100, 200))
        for oversold, osw, ew, trig, regime in grid:
            params = dict(oversold=oversold, oversold_window=osw, entry_window=ew,
                          trigger=trig, regime_days=regime)
            try:
                row = run_one(build("wvfx_stoch", **params), bars, bpy, bi)
            except Exception as exc:
                print(f"! {tf} {params}: {exc}", file=sys.stderr)
                continue
            row.update(tf=tf, stage=1, params=",".join(f"{k}={v}" for k, v in params.items()))
            rows.append(row)
        print(f"stage1 {tf}: {len(rows)} rows cumulative", flush=True)
    return pd.DataFrame(rows)


def stage2(tfs, best_entry: dict[str, dict], out_dir: str = "research"):
    rows = []
    for tf in tfs:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
        bpy = BARS_PER_YEAR.get(tf, 365.0)
        bi = int(len(bars) * IS_FRACTION)
        base = best_entry[tf]
        for stop, rr, hold, xc in itertools.product((1.5, 2.0, 2.5, 3.0),
                                                    (1.5, 2.0, 2.5, 3.0),
                                                    (20, 40, 60), (False, True)):
            params = dict(base, stop_atr=stop, rr=rr, max_hold=hold, exit_cross=xc)
            try:
                row = run_one(build("wvfx_stoch", **params), bars, bpy, bi)
            except Exception as exc:
                print(f"! {tf} {params}: {exc}", file=sys.stderr)
                continue
            row.update(tf=tf, stage=2, params=",".join(f"{k}={v}" for k, v in params.items()))
            rows.append(row)
        print(f"stage2 {tf}: done", flush=True)
    return pd.DataFrame(rows)


def stage3(tfs, best_exit: dict[str, dict]):
    rows = []
    for tf in tfs:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
        bpy = BARS_PER_YEAR.get(tf, 365.0)
        bi = int(len(bars) * IS_FRACTION)
        base = best_exit[tf]
        for bm, ph, kl, sk, sd in itertools.product((1.5, 2.0, 2.5), (0.80, 0.85, 0.90),
                                                    (9, 14, 21), (1, 3), (3, 5)):
            params = dict(base, band_mult=bm, percentile_high=ph, k_length=kl,
                          smooth_k=sk, smooth_d=sd)
            try:
                row = run_one(build("wvfx_stoch", **params), bars, bpy, bi)
            except Exception as exc:
                print(f"! {tf} {params}: {exc}", file=sys.stderr)
                continue
            row.update(tf=tf, stage=3, params=",".join(f"{k}={v}" for k, v in params.items()))
            rows.append(row)
        print(f"stage3 {tf}: done", flush=True)
    return pd.DataFrame(rows)


def stage4(tfs, best_exit: dict[str, dict]):
    """Trailing-stop variant: does the exit style fit WVFX better?"""
    rows = []
    for tf in tfs:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
        bpy = BARS_PER_YEAR.get(tf, 365.0)
        bi = int(len(bars) * IS_FRACTION)
        base = dict(best_exit[tf])
        for trail, keep, stop, hold in itertools.product((2.5, 3.0, 3.5, 4.0),
                                                         (True, False),
                                                         (2.0, 2.5, 3.0),
                                                         (40, 60)):
            params = dict(base, stop_atr=stop, max_hold=hold)
            try:
                row = run_one(WvfxTrail(trail_atr=trail, keep_target=keep, **params),
                             bars, bpy, bi)
            except Exception as exc:
                print(f"! {tf} {params} trail={trail}: {exc}", file=sys.stderr)
                continue
            full = dict(params, trail_atr=trail, keep_target=keep)
            row.update(tf=tf, stage=4, params=",".join(f"{k}={v}" for k, v in full.items()))
            rows.append(row)
        print(f"stage4 {tf}: done", flush=True)
    return pd.DataFrame(rows)


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


def pick_best(df: pd.DataFrame, tf: str, n: int = 4) -> list[dict]:
    d = df[(df.tf == tf) & df.score.notna()].sort_values("score", ascending=False)
    out = []
    for p in d.head(n)["params"]:
        out.append({k: _coerce(v) for k, v in (kv.split("=") for kv in p.split(","))})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h,6h,1d")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stage", type=int, default=1)
    args = ap.parse_args()
    tfs = [t.strip() for t in args.tf.split(",") if t.strip()]

    if args.all or args.stage == 1:
        s1 = stage1(tfs)
        s1.to_csv("research/wvfx_stage1.csv", index=False)
        print(f"\nwrote research/wvfx_stage1.csv ({len(s1)} rows)")
        for tf in tfs:
            d = s1[s1.tf == tf].sort_values("score", ascending=False).head(10)
            print(f"\n### stage1 {tf}: top 10 by worse-IS-half")
            print(_fmt(d, ["h1_ret", "h2_ret", "is_ret", "is_dd",
                           "oos_ret", "oos_dd", "is_trades", "oos_trades"]))

    if args.all or args.stage == 2:
        s1 = pd.read_csv("research/wvfx_stage1.csv")
        best = {tf: pick_best(s1, tf, 4) for tf in tfs}
        for tf in tfs:
            print(f"\n{tf} best entry params:", best[tf])
        rows = []
        for tf in tfs:
            bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
            bpy = BARS_PER_YEAR.get(tf, 365.0)
            bi = int(len(bars) * IS_FRACTION)
            for base in best[tf]:
                for stop, rr, hold, xc in itertools.product((1.5, 2.0, 2.5, 3.0),
                                                            (1.5, 2.0, 2.5, 3.0),
                                                            (20, 40, 60), (False, True)):
                    p = dict(base, stop_atr=stop, rr=rr, max_hold=hold, exit_cross=xc)
                    try:
                        row = run_one(build("wvfx_stoch", **p), bars, bpy, bi)
                    except Exception as exc:
                        print(f"! {tf} {p}: {exc}", file=sys.stderr)
                        continue
                    row.update(tf=tf, stage=2,
                               params=",".join(f"{k}={v}" for k, v in p.items()))
                    rows.append(row)
            print(f"stage2 {tf}: done", flush=True)
        s2 = pd.DataFrame(rows)
        s2.to_csv("research/wvfx_stage2.csv", index=False)
        print(f"\nwrote research/wvfx_stage2.csv ({len(s2)} rows)")
        for tf in tfs:
            d = s2[s2.tf == tf].sort_values("score", ascending=False).head(10)
            print(f"\n### stage2 {tf}: top 10")
            print(_fmt(d, ["h1_ret", "h2_ret", "is_ret", "is_dd",
                           "oos_ret", "oos_dd", "is_trades", "oos_trades"]))

    if args.all or args.stage in (3, 4):
        s2 = pd.read_csv("research/wvfx_stage2.csv")
        best = {tf: pick_best(s2, tf, 1)[0] for tf in tfs}
        for tf in tfs:
            print(f"\n{tf} best exit params:", best[tf])
        for stage_fn, name in ((stage3, "wvfx_stage3.csv"), (stage4, "wvfx_stage4.csv")):
            s = stage_fn(tfs, best)
            s.to_csv(f"research/{name}", index=False)
            print(f"\nwrote research/{name} ({len(s)} rows)")
            for tf in tfs:
                d = s[s.tf == tf].sort_values("score", ascending=False).head(8)
                print(f"\n### {name} {tf}: top 8")
                print(_fmt(d, ["h1_ret", "h2_ret", "is_ret", "is_dd",
                               "oos_ret", "oos_dd", "is_trades", "oos_trades"]))


if __name__ == "__main__":
    main()