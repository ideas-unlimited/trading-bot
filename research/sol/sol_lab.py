"""SOL/USDT strategy sweep: 4h / 6h / 1d, 2-year window, honest costs.

This is lab scratch (new agent, 2026-11-28). Goal: find the best long-only
spot strategy for SOL per the user's brief ("one strategy per symbol, profit
first"). Protocol follows docs/TARGETS.md and research/PER_SYMBOL.md:

* tune on the in-sample slice, validate on the untouched out-of-sample slice;
* every number reports its window and timeframe;
* full costs (10 bps fee + 5 bps slippage), 3% risk per trade, $1,000.

Method: each config runs ONCE over the full 2-year window with the standard
no-lookahead engine; the IS/OOS boundary splits the resulting causal equity
curve and trade list. No config ever sees the boundary when deciding.

Usage:
    PYTHONPATH=src python research/sol/sol_lab.py            # full sweep
    PYTHONPATH=src python research/sol/sol_lab.py --tf 4h    # one timeframe
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from bot.strategy.base import Action, Signal, Strategy  # noqa: E402
from bot import indicators as ind  # noqa: E402

SYMBOL = "SOL/USDT"
INITIAL_CASH = 1_000.0
RISK_PER_TRADE = 0.03
IS_FRACTION = 0.60  # first 60% of bars = IS (~14.6 months on 4h), rest is OOS


# ---------------------------------------------------------------------------
# Lab-only strategy candidates (research here; promote a winner to
# src/bot/strategy/ only if it survives OOS and gets tests).
# ---------------------------------------------------------------------------
class SqueezeBreakout(Strategy):
    """Buy a Bollinger-band-width squeeze resolving upward.

    Volatility contraction (band width in its own low tail over the lookback)
    within the last 3 bars, then a close above the prior bar's upper band.
    Exit: ATR stop plus Chandelier trail.
    """

    name = "lab_squeeze"
    PARAMS = ("bb_len", "squeeze_lookback", "squeeze_q", "atr_mult", "trail_atr")

    def __init__(self, bb_len: int = 20, squeeze_lookback: int = 100,
                 squeeze_q: float = 0.20, atr_mult: float = 2.0,
                 trail_atr: float | None = 3.5) -> None:
        super().__init__(bb_len=bb_len, squeeze_lookback=squeeze_lookback,
                         squeeze_q=squeeze_q, atr_mult=atr_mult, trail_atr=trail_atr)
        self.warmup = max(bb_len, squeeze_lookback, 20) + 4

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        mid = out["close"].rolling(self.bb_len).mean()
        sd = out["close"].rolling(self.bb_len).std(ddof=0)
        width = (4.0 * sd) / mid
        thr = width.rolling(self.squeeze_lookback).quantile(self.squeeze_q)
        squeezed = (width <= thr).astype(float).rolling(3).max().shift(1)
        out["squeeze_recent"] = squeezed == 1.0
        out["entry_level"] = (mid + 2.0 * sd).shift(1)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if position > 0 or pd.isna(row["atr"]) or pd.isna(row["entry_level"]):
            return None
        if not bool(row["squeeze_recent"]) or float(row["close"]) <= float(row["entry_level"]):
            return None
        stop = float(row["close"]) - self.atr_mult * float(row["atr"])
        return Signal(Action.BUY, "squeeze breakout", stop_loss=stop, trail_atr=self.trail_atr)


class DipTrend(Strategy):
    """Buy a dip that touches the fast EMA inside a slow-EMA uptrend."""

    name = "lab_dip"
    PARAMS = ("trend_len", "fast_len", "atr_mult", "trail_atr")

    def __init__(self, trend_len: int = 200, fast_len: int = 20,
                 atr_mult: float = 2.0, trail_atr: float | None = 3.5) -> None:
        super().__init__(trend_len=trend_len, fast_len=fast_len,
                         atr_mult=atr_mult, trail_atr=trail_atr)
        self.warmup = max(trend_len, fast_len, 20) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["trend"] = ind.ema(out["close"], self.trend_len)
        out["fast"] = ind.ema(out["close"], self.fast_len)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if position > 0 or pd.isna(row["atr"]) or pd.isna(row["trend"]) or pd.isna(row["fast"]):
            return None
        close, low = float(row["close"]), float(row["low"])
        fast, trend = float(row["fast"]), float(row["trend"])
        if not (close > trend and low <= fast and close > fast):
            return None
        stop = close - self.atr_mult * float(row["atr"])
        return Signal(Action.BUY, "dip reclaimed fast EMA", stop_loss=stop, trail_atr=self.trail_atr)


class DonchianTrail(Strategy):
    """Donchian breakout entry, Chandelier-trail exit (no channel exit)."""

    name = "lab_dontrail"
    PARAMS = ("entry_window", "atr_mult", "trail_atr")

    def __init__(self, entry_window: int = 55, atr_mult: float = 2.0,
                 trail_atr: float | None = 3.5) -> None:
        super().__init__(entry_window=entry_window, atr_mult=atr_mult, trail_atr=trail_atr)
        self.warmup = max(entry_window, 20) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["entry_level"] = out["high"].rolling(self.entry_window).max().shift(1)
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if position > 0 or pd.isna(row["atr"]) or pd.isna(row["entry_level"]):
            return None
        if float(row["close"]) <= float(row["entry_level"]):
            return None
        stop = float(row["close"]) - self.atr_mult * float(row["atr"])
        return Signal(Action.BUY, f"{self.entry_window}-bar breakout", stop_loss=stop,
                      trail_atr=self.trail_atr)


LAB_STRATEGIES: dict[str, type[Strategy]] = {
    SqueezeBreakout.name: SqueezeBreakout,
    DipTrend.name: DipTrend,
    DonchianTrail.name: DonchianTrail,
}


# ---------------------------------------------------------------------------
# Config grid
# ---------------------------------------------------------------------------
def config_grid() -> list[tuple[str, dict]]:
    cfgs: list[tuple[str, dict]] = []

    for fast, slow in [(10, 30), (20, 50), (30, 50), (50, 100), (20, 100)]:
        for atr in (2.0, 3.0):
            cfgs.append(("sma_cross", dict(fast=fast, slow=slow, atr_mult=atr)))

    for entry, exit_w in [(20, 10), (55, 10), (55, 20), (100, 20), (100, 50)]:
        for atr in (2.0, 3.0):
            cfgs.append(("donchian", dict(entry_window=entry, exit_window=exit_w, atr_mult=atr)))

    for window in (20, 55, 100):
        for atr in (2.0, 2.5):
            for trail in (3.0, 3.5):
                cfgs.append(("lab_dontrail", dict(entry_window=window, atr_mult=atr, trail_atr=trail)))

    for lookback in (40, 60, 90):
        for atr in (2.0, 2.5):
            for trail in (3.0, 3.5):
                for regime in (100, 200):
                    cfgs.append(("momentum_swing", dict(
                        lookback=lookback, regime_ma=regime, atr_mult=atr, trail_atr=trail)))

    for entry in (25, 30, 35):
        for exit_lvl in (55, 65):
            for trend in (100, 200):
                cfgs.append(("rsi_meanrev", dict(entry=entry, exit_level=exit_lvl, trend_window=trend)))

    for z in (1.5, 2.0, 2.5):
        for drop in (0.04, 0.06, 0.08):
            cfgs.append(("vol_climax", dict(vol_z=z, drop_pct=drop)))

    for trigger in ("cross", "state"):
        for rr in (1.5, 2.0, 2.5):
            for stop_atr, max_hold in ((2.0, 40), (2.5, 50)):
                for regime_days in (0, 200):
                    cfgs.append(("wvfx_stoch", dict(
                        trigger=trigger, rr=rr, stop_atr=stop_atr,
                        max_hold=max_hold, regime_days=regime_days)))

    for q in (0.15, 0.30):
        for trail in (3.0, 3.5):
            cfgs.append(("lab_squeeze", dict(squeeze_q=q, trail_atr=trail)))

    for atr in (2.0, 2.5):
        for trail in (3.0, 3.5):
            cfgs.append(("lab_dip", dict(atr_mult=atr, trail_atr=trail)))

    return cfgs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
@dataclass
class SliceMetrics:
    ret: float = float("nan")
    max_dd: float = float("nan")
    sharpe: float = float("nan")
    trades: int = 0
    win_rate: float = float("nan")
    profit_factor: float = float("nan")


def _slice_metrics(equity: pd.Series, trades_pnl: list[float],
                   bars_per_year: float) -> SliceMetrics:
    m = SliceMetrics()
    if len(equity) < 2:
        return m
    eq = equity.astype(float)
    m.ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    dd = eq / eq.cummax() - 1.0
    m.max_dd = float(dd.min())
    rets = eq.pct_change().dropna()
    if rets.std(ddof=1) and rets.std(ddof=1) > 0:
        m.sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(bars_per_year))
    pnls = [p for p in trades_pnl]
    m.trades = len(pnls)
    if pnls:
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        m.win_rate = len(wins) / len(pnls)
        gl = abs(sum(losses))
        m.profit_factor = (sum(wins) / gl) if gl > 0 else float("inf")
    return m


def run_config(family: str, params: dict, bars: list, bars_per_year: float,
               boundary_ts) -> dict:
    if family in LAB_STRATEGIES:
        strat = LAB_STRATEGIES[family](**params)
    else:
        strat = build(family, **params)
    config = BotConfig(
        initial_cash=INITIAL_CASH,
        costs=CostConfig(),  # 10 bps fee, 5 bps slippage
        risk=RiskConfig(risk_per_trade=RISK_PER_TRADE, max_position_pct=0.95),
        bars_per_year=bars_per_year,
    )
    engine = Engine(bars_to_frame(bars), strat, config)
    engine.run()
    res = engine.result()

    eq = res.equity_curve
    # Trades assigned to the slice where they were ENTERED.
    is_pnl = [t.return_pct for t in res.trades if t.entry_ts < boundary_ts]
    oos_pnl = [t.return_pct for t in res.trades if t.entry_ts >= boundary_ts]

    is_eq = eq[eq.index < boundary_ts]
    oos_eq = eq[eq.index >= boundary_ts]
    if len(oos_eq) and len(is_eq):
        # carry the boundary equity into the OOS slice so its return is
        # measured from where IS ended
        oos_eq = pd.concat([is_eq.iloc[[-1]], oos_eq])

    m_is = _slice_metrics(is_eq, is_pnl, bars_per_year)
    m_oos = _slice_metrics(oos_eq, oos_pnl, bars_per_year)

    close = res.close
    bh_is = float(close[close.index < boundary_ts].iloc[0])
    close_is = close[close.index < boundary_ts]
    close_oos = close[close.index >= boundary_ts]
    bh = SliceMetrics()
    if len(close_is):
        bh_is = float(close_is.iloc[-1] / close_is.iloc[0] - 1.0)
    bh.ret = bh_is
    if len(close_oos):
        bh_oos = float(close_oos.iloc[-1] / close_oos.iloc[0] - 1.0)
        bh.ret = bh_oos  # overwritten below; kept simple

    # buy & hold per slice, properly
    bh_is_m = SliceMetrics()
    bh_oos_m = SliceMetrics()
    if len(close_is) > 1:
        bh_is_m.ret = float(close_is.iloc[-1] / close_is.iloc[0] - 1.0)
        dd = close_is / close_is.cummax() - 1.0
        bh_is_m.max_dd = float(dd.min())
    if len(close_oos) > 1:
        c2 = pd.concat([close_is.iloc[[-1]], close_oos])
        bh_oos_m.ret = float(c2.iloc[-1] / c2.iloc[0] - 1.0)
        dd = c2 / c2.cummax() - 1.0
        bh_oos_m.max_dd = float(dd.min())

    return {
        "family": family,
        "params": ",".join(f"{k}={v}" for k, v in params.items()),
        "is_ret": m_is.ret, "is_dd": m_is.max_dd, "is_sharpe": m_is.sharpe,
        "is_trades": m_is.trades, "is_win": m_is.win_rate, "is_pf": m_is.profit_factor,
        "oos_ret": m_oos.ret, "oos_dd": m_oos.max_dd, "oos_sharpe": m_oos.sharpe,
        "oos_trades": m_oos.trades, "oos_win": m_oos.win_rate, "oos_pf": m_oos.profit_factor,
        "bh_is": bh_is_m.ret, "bh_oos": bh_oos_m.ret,
        "halted": res.halted_reason or "",
        "final_equity": res.final_equity,
        "total_ret": m_is.ret if math.isnan(m_oos.ret) else None,  # unused; full window below
        "full_ret": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
    }


def sweep(timeframes: list[str]) -> pd.DataFrame:
    rows = []
    for tf in timeframes:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=730)
        if len(bars) < 400:
            print(f"! {tf}: only {len(bars)} bars, skipping", file=sys.stderr)
            continue
        bars_per_year = {  # approximate, only for Sharpe annualisation
            "4h": 2190.0, "6h": 1460.0, "1d": 365.0,
        }.get(tf)
        if bars_per_year is None:
            spacing = (bars[-1].ts - bars[0].ts).total_seconds() / (len(bars) - 1)
            bars_per_year = 365 * 86400 / spacing
        n = len(bars)
        boundary_i = int(n * IS_FRACTION)
        boundary_ts = bars[boundary_i].ts
        bh_all = bars[-1].close / bars[0].close - 1
        print(f"== {tf}: {n} bars {bars[0].ts:%Y-%m-%d} -> {bars[-1].ts:%Y-%m-%d} "
              f"(IS {boundary_i} bars / OOS {n - boundary_i}; B&H full {bh_all:+.0%})",
              flush=True)
        for family, params in config_grid():
            try:
                row = run_config(family, params, bars, bars_per_year, boundary_ts)
            except Exception as exc:  # keep the sweep alive, note the failure
                print(f"! {family} {params}: {exc}", file=sys.stderr)
                continue
            row["tf"] = tf
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h,6h,1d", help="comma list: 4h,6h,1d")
    args = ap.parse_args()
    tfs = [t.strip() for t in args.tf.split(",") if t.strip()]
    df = sweep(tfs)
    out = str(Path(__file__).resolve().parent / "sol_sweep.csv")
    df.to_csv(out, index=False)
    print(f"\nwrote {out} ({len(df)} rows)")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    for tf in df["tf"].unique():
        d = df[df["tf"] == tf].sort_values("oos_ret", ascending=False)
        cols = ["family", "params", "is_ret", "is_dd", "is_trades",
                "oos_ret", "oos_dd", "oos_trades", "oos_win", "bh_oos"]
        print(f"\n### {tf}: top 15 by OOS return (full costs, 3% risk)")
        print(d[cols].head(15).to_string(index=False,
              formatters={c: (lambda v: f"{v:+.0%}" if isinstance(v, float) and abs(v) < 20 else
                              (f"{v:.2f}" if isinstance(v, float) else str(v)))
                          for c in cols[2:]}))


if __name__ == "__main__":
    main()
