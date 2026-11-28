
"""JUP/USDT multi-timeframe liquidity-zone DCA study.

Research only.  Builds demand bands around confirmed swing lows on 4h/6h/1d,
requires the bands to overlap, then buys 1 or 3 tranches inside the overlap.
Signals are formed on completed 4h bars; fills happen on later bars.  Costs:
10 bps fee + 5 bps slippage per side.  No same-bar target after a new fill.

Two entry styles:
  passive - resting limits at the cluster's upper/center/lower levels; later
            tranches can still fill after the first one (true DCA).
  reclaim - after a 4h bar pierces the lower band and closes back above the
            cluster center, buy the next open and add at center/lower.

Confluence can be tested as an exact bucket (exactly 1/2/3 timeframes) or as
"at least N, take the highest-confluence cluster available".

Window: 2024-11-28 08:00 -> 2026-11-27 20:00 UTC, Binance spot, 4,378 4h
decision bars.  The last (still-forming) bar of each timeframe is dropped.
"""
from __future__ import annotations

import copy
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from bot.analysis import atr, swing_pivots, sweep_reclaim
from bot.backtest import bars_to_frame
from bot.indicators import ema, rsi
from bot.scanlib import load_scan_bars

SYMBOL = "JUP/USDT"
TF_HOURS = {"4h": 4, "6h": 6, "1d": 24}
FEE = 0.0010
SLIP = 0.0005
RISK = 0.05
MAX_POS = 0.50
ZONE_BREAK_ATR = 0.75  # a completed close this far below the band kills it


@dataclass(frozen=True)
class Zone:
    zid: int
    tf: str
    center: float
    lower: float
    upper: float
    pivot_at: pd.Timestamp
    confirmed_at: pd.Timestamp
    available_at: pd.Timestamp
    expires_at: pd.Timestamp


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    avg_entry: float
    exit_px: float
    qty: float
    pnl: float
    ret: float
    fills: int
    reason: str
    confluence: int
    tfs: tuple[str, ...] = ()


@dataclass
class Result:
    label: str
    min_conf: int
    tranches: int
    filt: str
    rr: float
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    total_return: float = 0.0
    cagr: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    exposure: float = 0.0
    win_rate: float = 0.0
    avg_trade: float = 0.0
    profit_factor: float = 0.0
    avg_fills: float = 0.0

    def row(self) -> dict:
        return {
            "label": self.label,
            "min_conf": self.min_conf,
            "tranches": self.tranches,
            "filter": self.filt,
            "rr": self.rr,
            "return_pct": round(self.total_return * 100, 2),
            "cagr_pct": round(self.cagr * 100, 2),
            "max_dd_pct": round(self.max_dd * 100, 2),
            "sharpe": round(self.sharpe, 2),
            "trades": len(self.trades),
            "win_pct": round(self.win_rate * 100, 1),
            "avg_trade_pct": round(self.avg_trade * 100, 2),
            "profit_factor": round(self.profit_factor, 2),
            "exposure_pct": round(self.exposure * 100, 1),
            "avg_fills": round(self.avg_fills, 2),
        }


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frames = []
    for tf in ("4h", "6h", "1d"):
        df = bars_to_frame(
            load_scan_bars(SYMBOL, tf, source="binance", days=730)
        ).set_index("ts")
        frames.append(df)
    end = min(df.index[-1] for df in frames)  # drop every still-forming bar
    return tuple(df.loc[df.index < end].copy() for df in frames)  # type: ignore[return-value]


def build_zones(
    df: pd.DataFrame,
    tf: str,
    *,
    swing: int = 5,
    half_atr: float = 0.50,
    max_age_days: int = 60,
) -> list[Zone]:
    """Demand bands around confirmed swing lows only.  Causal."""
    p = swing_pivots(df["high"], df["low"], swing, swing)
    a = atr(df["high"], df["low"], df["close"], 14)
    delta = pd.Timedelta(hours=TF_HOURS[tf])
    zones: list[Zone] = []
    for conf_pos in np.flatnonzero(p["pivot_low"].to_numpy()):
        pivot_pos = int(conf_pos) - swing
        if pivot_pos < 0:
            continue
        level = float(df["low"].iloc[pivot_pos])
        av = float(a.iloc[conf_pos])
        if not math.isfinite(level) or not math.isfinite(av) or level <= 0 or av <= 0:
            continue
        available_at = df.index[conf_pos] + delta
        if available_at > df.index[-1] + delta:
            continue
        half = half_atr * av
        lower, upper = level - half, level + half
        # One live band per TF at a price; a new pivot near an old one is noise.
        duplicate = any(
            z.tf == tf
            and z.available_at <= available_at < z.expires_at
            and abs(z.center - level) <= 0.5 * av
            for z in zones
        )
        if duplicate:
            continue
        zones.append(
            Zone(
                zid=len(zones),
                tf=tf,
                center=level,
                lower=lower,
                upper=upper,
                pivot_at=df.index[pivot_pos],
                confirmed_at=df.index[conf_pos],
                available_at=available_at,
                expires_at=available_at + pd.Timedelta(days=max_age_days),
            )
        )
    return zones


@dataclass
class Cluster:
    lower: float
    upper: float
    zones: list[Zone]

    @property
    def tfs(self) -> set[str]:
        return {z.tf for z in self.zones}

    @property
    def center(self) -> float:
        return 0.5 * (self.lower + self.upper)


def active_clusters(
    zones: list[Zone],
    states: list[bool],
    ts_close: pd.Timestamp,
    close: float,
    atr4: float,
    *,
    max_dist_atr: float,
    min_width_atr: float,
    above: str = "upper",
    center_tol_atr: float | None = None,
) -> list[Cluster]:
    active: list[Zone] = []
    for z, broken in zip(zones, states):
        if broken or ts_close < z.available_at or ts_close >= z.expires_at:
            continue
        if close < z.lower:  # a completed 4h close below the band kills it
            continue
        active.append(z)
    if not active or not math.isfinite(atr4) or atr4 <= 0:
        return []

    active.sort(key=lambda z: (z.lower, z.upper))
    clusters: list[Cluster] = []
    for z in active:
        placed = False
        for c in clusters:
            if center_tol_atr is not None and abs(z.center - c.center) > center_tol_atr * atr4:
                continue
            if z.lower <= c.upper and z.upper >= c.lower:
                new_lower = max(c.lower, z.lower)
                new_upper = min(c.upper, z.upper)
                if new_lower <= new_upper:
                    c.lower, c.upper = new_lower, new_upper
                    c.zones.append(z)
                    placed = True
                    break
        if not placed:
            clusters.append(Cluster(z.lower, z.upper, [z]))

    out: list[Cluster] = []
    for c in clusters:
        threshold = c.upper if above == "upper" else c.center
        if threshold >= close:
            continue
        if (close - threshold) / atr4 > max_dist_atr:
            continue
        if (c.upper - c.lower) / atr4 < min_width_atr:
            continue
        out.append(c)
    return out


def _stats(eq: pd.Series, trades: list[Trade], exposure: float) -> dict:
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return {}
    ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400.0, 1.0)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (365.0 / days) - 1.0 if eq.iloc[-1] > 0 else -1.0
    r = eq.pct_change().dropna()
    sharpe = float(r.mean() / r.std(ddof=1) * math.sqrt(2190)) if len(r) > 2 and r.std(ddof=1) > 0 else 0.0
    dd = float((eq / eq.cummax() - 1.0).min())
    pnls = [t.pnl for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else float("inf") if wins else 0.0
    return {
        "total_return": ret,
        "cagr": cagr,
        "max_dd": dd,
        "sharpe": sharpe,
        "exposure": exposure,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_trade": float(np.mean([t.ret for t in trades])) if trades else 0.0,
        "profit_factor": pf,
        "avg_fills": float(np.mean([t.fills for t in trades])) if trades else 0.0,
    }


def simulate(
    df: pd.DataFrame,
    zones: list[Zone],
    *,
    label: str,
    min_conf: int = 1,
    max_conf: int | None = None,
    tranches: int = 3,
    filt: str = "none",
    rr: float = 1.5,
    max_dist_atr: float = 3.0,
    min_width_atr: float = 0.15,
    entry_window: int = 18,
    stop_atr: float = 1.0,
    max_hold: int = 30,
    swing_lookback: int = 6,
    entry_mode: str = "passive",
    reclaim_level: str = "upper",
    strict_break: bool = False,
    center_tol_atr: float | None = None,
) -> Result:
    df = df.copy()
    atr4 = atr(df["high"], df["low"], df["close"], 14)
    rsi14 = rsi(df["close"], 14)
    ema200 = ema(df["close"], 200)
    sw = sweep_reclaim(df, 20).fillna(False)
    recent_sweep = sw["sell_sweep"].rolling(swing_lookback, min_periods=1).max().fillna(0).astype(bool)

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a4 = atr4.to_numpy(float)
    rsi_arr = rsi14.to_numpy(float)
    ema_arr = ema200.to_numpy(float)
    sweep_arr = recent_sweep.to_numpy(bool)

    # zone state is mutable per run (a close below the band is permanent)
    states = [False] * len(zones)
    zcopy = copy.deepcopy(zones)
    cash = 1000.0
    qty = 0.0
    cost_basis = 0.0
    avg_entry = 0.0
    setup: dict | None = None
    trades: list[Trade] = []
    eq_vals: list[float] = []
    exposure_bars = 0
    cooldown_until = -1

    def close_trade(ts: pd.Timestamp, px: float, reason: str) -> None:
        nonlocal cash, qty, cost_basis, avg_entry, setup
        if qty <= 0:
            return
        px = max(px * (1.0 - SLIP), 1e-12)
        proceeds = qty * px
        fee = proceeds * FEE
        cash += proceeds - fee
        pnl = proceeds - fee - cost_basis
        trades.append(
            Trade(
                entry_ts=setup["entry_ts"] if setup else ts,
                exit_ts=ts,
                avg_entry=avg_entry,
                exit_px=px,
                qty=qty,
                pnl=pnl,
                ret=pnl / cost_basis if cost_basis > 0 else 0.0,
                fills=int(setup["filled_count"]) if setup else 0,
                reason=reason,
                confluence=int(setup["confluence"]) if setup else 0,
                tfs=tuple(sorted(setup.get("tfs", ()))) if setup else (),
            )
        )
        qty = 0.0
        cost_basis = 0.0
        avg_entry = 0.0
        setup = None

    def add_fill(i: int, ts: pd.Timestamp, k: int, raw_px: float, want: float) -> None:
        nonlocal cash, qty, cost_basis, avg_entry
        if setup is None or want <= 0:
            return
        buy_px = raw_px * (1.0 + SLIP)
        need = want * buy_px * (1.0 + FEE)
        if need > cash:
            want = max(0.0, cash / (buy_px * (1.0 + FEE)))
        if want <= 0:
            return
        cost = want * buy_px
        fee = cost * FEE
        cash -= cost + fee
        cost_basis += cost + fee
        qty += want
        avg_entry = (avg_entry * (qty - want) + want * buy_px) / qty
        setup["filled"][k] = True
        setup["filled_count"] += 1
        if setup["first_fill_i"] is None:
            setup["first_fill_i"] = i
            setup["entry_ts"] = ts
        setup["target"] = avg_entry + rr * (avg_entry - setup["stop"])

    for i, ts in enumerate(df.index):
        bar_close = ts + pd.Timedelta(hours=4)

        # 1. Existing position: gap through the stop or a gap above target.
        if qty > 0:
            if o[i] <= setup["stop"]:
                close_trade(ts, o[i], "stop_gap")
            elif o[i] >= setup["target"]:
                close_trade(ts, o[i], "target_gap")
        if qty > 0 and setup is not None:
            # 2. Intrabar stop first; both-hit bars resolve to the stop.
            if l[i] <= setup["stop"]:
                close_trade(ts, setup["stop"], "stop")
            elif h[i] >= setup["target"]:
                close_trade(ts, setup["target"], "target")
            elif i - setup["first_fill_i"] >= max_hold:
                close_trade(ts, c[i], "time")
        if qty > 0:
            exposure_bars += 1

        # 3. Market-first tranche (reclaim mode) and/or resting limits.  A
        # setup keeps accepting later DCA fills after the first tranche.
        if setup is not None:
            if i >= setup["expires_i"]:
                setup["accepting"] = False
                if qty == 0:
                    setup = None
            if setup is not None and setup["accepting"] and i > setup["arm_i"]:
                if qty == 0 and o[i] <= setup["stop"]:
                    setup = None  # gapped through the whole zone; no chase
                else:
                    if setup["market_first"] and not setup["filled"][0]:
                        add_fill(i, ts, 0, o[i], setup["qtys"][0])
                    for k, level in enumerate(setup["levels"]):
                        if k == 0 and setup["market_first"]:
                            continue
                        if setup["filled"][k] or l[i] > level:
                            continue
                        add_fill(i, ts, k, min(o[i], level), setup["qtys"][k])
                    if qty > 0:
                        # Same-bar stop after a fill; no same-bar target
                        # (the high may have printed before the fill).
                        if l[i] <= setup["stop"]:
                            close_trade(ts, setup["stop"], "stop_same_bar")
                        elif i - setup["first_fill_i"] >= max_hold:
                            close_trade(ts, c[i], "time")

        # 4. Strict-break modes cancel unfilled tranches; a position already
        # held keeps its stop.  A close merely below the band is the pierce,
        # so passive non-strict mode deliberately does not cancel there.
        if setup is not None:
            if setup.get("strict_break") and c[i] < setup["zone_lower"]:
                setup["accepting"] = False
            if qty == 0 and (not setup["accepting"] or c[i] < setup["stop"]):
                setup = None

        eq_vals.append(cash + qty * c[i])

        # 5. Mark broken zones after this close; arm a new setup at this close.
        for zi, z in enumerate(zcopy):
            if states[zi]:
                continue
            if bar_close >= z.expires_at:
                states[zi] = True
            elif bar_close >= z.available_at and c[i] < z.lower - ZONE_BREAK_ATR * a4[i]:
                states[zi] = True

        if qty == 0 and setup is None and i >= cooldown_until:
            allowed = True
            if filt == "rsi40" and not (math.isfinite(rsi_arr[i]) and rsi_arr[i] <= 40.0):
                allowed = False
            elif filt == "rsi35" and not (math.isfinite(rsi_arr[i]) and rsi_arr[i] <= 35.0):
                allowed = False
            elif filt == "ema200" and not (math.isfinite(ema_arr[i]) and c[i] > ema_arr[i]):
                allowed = False
            elif filt == "sweep" and not sweep_arr[i]:
                allowed = False
            elif filt == "rsi_ema":
                allowed = (
                    math.isfinite(rsi_arr[i])
                    and rsi_arr[i] <= 40.0
                    and math.isfinite(ema_arr[i])
                    and c[i] > ema_arr[i]
                )
            if allowed:
                above = "upper" if entry_mode == "passive" or reclaim_level == "upper" else "center"
                clusters = active_clusters(
                    zcopy,
                    states,
                    bar_close,
                    c[i],
                    a4[i],
                    max_dist_atr=max_dist_atr,
                    min_width_atr=min_width_atr,
                    above=above,
                    center_tol_atr=center_tol_atr,
                )
                clusters = [
                    x for x in clusters
                    if len(x.tfs) >= min_conf and (max_conf is None or len(x.tfs) <= max_conf)
                ]
                if entry_mode == "reclaim":
                    clusters = [
                        x for x in clusters
                        if l[i] < x.lower
                        and c[i] > (x.upper if reclaim_level == "upper" else x.center)
                    ]
                if clusters:
                    ref = (lambda x: x.upper) if above == "upper" else (lambda x: x.center)
                    clusters.sort(key=lambda x: (len(x.tfs), -((c[i] - ref(x)) / a4[i])), reverse=True)
                    cl = clusters[0]
                    market_first = entry_mode == "reclaim"
                    if market_first:
                        levels = [c[i]]
                        if tranches >= 2:
                            levels.append(cl.lower)
                        if tranches >= 3:
                            levels.insert(1, cl.center)
                    else:
                        levels = [cl.upper]
                        if tranches >= 2:
                            levels.append(cl.lower)
                        if tranches >= 3:
                            levels.insert(1, cl.center)
                    stop = cl.lower - stop_atr * a4[i]
                    if stop < cl.lower and cl.upper > stop:
                        equity_now = cash + qty * c[i]
                        # risk-parity sizing: each tranche risks RISK/tranches
                        # of *current* equity, so the book compounds honestly.
                        qtys = []
                        for lev in levels:
                            risk_per_unit = lev - stop
                            if risk_per_unit <= 0:
                                qtys.append(0.0)
                            else:
                                qtys.append((equity_now * RISK / len(levels)) / risk_per_unit)
                        # position cap at the band, based on planned cost
                        planned_cost = sum(q * lev for q, lev in zip(qtys, levels))
                        cap = MAX_POS * equity_now
                        if planned_cost > cap and planned_cost > 0:
                            scale = cap / planned_cost
                            qtys = [q * scale for q in qtys]
                        if sum(qtys) > 0:
                            setup = {
                                "arm_i": i,
                                "expires_i": i + entry_window,
                                "levels": levels,
                                "qtys": qtys,
                                "filled": [False] * len(levels),
                                "filled_count": 0,
                                "first_fill_i": None,
                                "entry_ts": ts,
                                "stop": stop,
                                "target": levels[0] + rr * (levels[0] - stop),
                                "zone_lower": cl.lower,
                                "zone_upper": cl.upper,
                                "confluence": len(cl.tfs),
                                "tfs": tuple(sorted(cl.tfs)),
                                "market_first": market_first,
                                "accepting": True,
                                "strict_break": strict_break or market_first,
                            }

        # 6. Cooldown one bar after a close so we never re-arm on the same bar.
        if qty == 0 and setup is None and len(trades) and trades[-1].exit_ts == ts:
            cooldown_until = i + 1

    if qty > 0:
        close_trade(df.index[-1], c[-1], "end")
        eq_vals[-1] = cash

    eq = pd.Series(eq_vals, index=df.index, name="equity")
    stats = _stats(eq, trades, exposure_bars / max(len(df), 1))
    return Result(
        label=label,
        min_conf=min_conf,
        tranches=tranches,
        filt=filt,
        rr=rr,
        equity=eq,
        trades=trades,
        **stats,
    )


def period_stats(r: Result, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    eq = r.equity.loc[(r.equity.index >= start) & (r.equity.index < end)]
    if len(eq) < 2:
        return {
            "ret_pct": 0.0, "dd_pct": 0.0, "trades": 0, "win_pct": 0.0,
            "avg_pct": 0.0, "pf": 0.0,
        }
    ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    ts = [t for t in r.trades if start <= t.entry_ts < end]
    pnls = [t.pnl for t in ts]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else float("inf") if wins else 0.0
    return {
        "ret_pct": round(ret * 100, 2),
        "dd_pct": round(dd * 100, 2),
        "trades": len(ts),
        "win_pct": round(len(wins) / len(ts) * 100, 1) if ts else 0.0,
        "avg_pct": round(float(np.mean([t.ret for t in ts])) * 100, 2) if ts else 0.0,
        "pf": round(pf, 2),
    }


def fmt_row(r: Result) -> str:
    x = r.row()
    return (
        f"{x['label']:<28} ret {x['return_pct']:>7.1f}%  dd {x['max_dd_pct']:>6.1f}%  "
        f"sh {x['sharpe']:>4.1f}  n {x['trades']:>3}  win {x['win_pct']:>5.1f}%  "
        f"avg {x['avg_trade_pct']:>6.1f}%  exp {x['exposure_pct']:>5.1f}%  fills {x['avg_fills']:.1f}"
    )


def study_row(
    r: Result,
    kind: str,
    max_conf: int | None,
    entry_mode: str,
    reclaim_level: str,
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    tr = period_stats(r, start, split)
    te = period_stats(r, split, end)
    return {
        "label": r.label,
        "kind": kind,
        "min_conf": r.min_conf,
        "max_conf": "" if max_conf is None else max_conf,
        "tranches": r.tranches,
        "entry_mode": entry_mode,
        "reclaim_level": reclaim_level,
        "rr": r.rr,
        "return_pct": round(r.total_return * 100, 2),
        "cagr_pct": round(r.cagr * 100, 2),
        "max_dd_pct": round(r.max_dd * 100, 2),
        "sharpe": round(r.sharpe, 2),
        "trades": len(r.trades),
        "win_pct": round(r.win_rate * 100, 1),
        "avg_trade_pct": round(r.avg_trade * 100, 2),
        "profit_factor": round(r.profit_factor, 2),
        "exposure_pct": round(r.exposure * 100, 1),
        "avg_fills": round(r.avg_fills, 2),
        "y1_ret_pct": tr["ret_pct"], "y1_dd_pct": tr["dd_pct"],
        "y1_trades": tr["trades"], "y1_win_pct": tr["win_pct"],
        "y2_ret_pct": te["ret_pct"], "y2_dd_pct": te["dd_pct"],
        "y2_trades": te["trades"], "y2_win_pct": te["win_pct"],
    }


def print_study(d: dict) -> None:
    print(
        f"{d['label']:<24} ret {d['return_pct']:>7.1f}%  dd {d['max_dd_pct']:>6.1f}%  "
        f"sh {d['sharpe']:>4.2f}  n {d['trades']:>3}  win {d['win_pct']:>5.1f}%  "
        f"avg {d['avg_trade_pct']:>5.2f}%  pf {d['profit_factor']:>4.2f}  "
        f"fills {d['avg_fills']:.2f}  |  y1 {d['y1_ret_pct']:>7.1f}%/{d['y1_trades']:>3}  "
        f"y2 {d['y2_ret_pct']:>7.1f}%/{d['y2_trades']:>3}"
    )


def main() -> None:
    df4, df6, df1 = load_data()
    start = df4.index[0]
    split = start + pd.Timedelta(days=365)
    end = df4.index[-1] + pd.Timedelta(hours=4)
    print(f"JUP/USDT  window {start} -> {df4.index[-1]}  bars {len(df4)}  split {split}")
    print(f"buy&hold {df4.close.iloc[-1] / df4.close.iloc[0] - 1:+.1%}  "
          f"year1 {df4.loc[df4.index < split].close.iloc[-1] / df4.close.iloc[0] - 1:+.1%}  "
          f"year2 {df4.close.iloc[-1] / df4.loc[df4.index >= split].close.iloc[0] - 1:+.1%}")

    zones = []
    for tf, df in (("4h", df4), ("6h", df6), ("1d", df1)):
        z = build_zones(df, tf)
        zones.extend(z)
        print(f"zones {tf}: {len(z)} demand bands, ages {min((x.expires_at-x.available_at).days for x in z) if z else 0}d")

    rows: list[dict] = []

    # Exact buckets: exactly 1, exactly 2, exactly 3 timeframes.
    for conf in (1, 2, 3):
        for tranches in (1, 3):
            r = simulate(
                df4, zones, label=f"exact{conf}-d{tranches}",
                min_conf=conf, max_conf=conf, tranches=tranches,
            )
            rows.append(study_row(r, "exact", conf, "passive", "", start, split, end))

    # Best available: at least N, take the highest-confluence cluster.
    for conf in (1, 2):
        for tranches in (1, 3):
            r = simulate(
                df4, zones, label=f"best>={conf}-d{tranches}",
                min_conf=conf, tranches=tranches,
            )
            rows.append(study_row(r, "best", None, "passive", "", start, split, end))

    # Piercing variant: wait for a 4h close back above the cluster centre.
    for conf in (1, 2, 3):
        r = simulate(
            df4, zones, label=f"reclaim-center-c{conf}-d1",
            min_conf=conf, tranches=1, entry_mode="reclaim", reclaim_level="center",
        )
        rows.append(study_row(r, "reclaim-center", None, "reclaim", "center", start, split, end))

    out = Path(__file__).resolve().parent / "jup_liquidity.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} rows)")

    print("\nExact confluence buckets (passive limits, 1 vs 3 tranches):")
    for d in rows:
        if d["kind"] == "exact":
            print_study(d)

    print("\nBest available cluster (at least N timeframes, highest confluence wins):")
    for d in rows:
        if d["kind"] == "best":
            print_study(d)

    print("\nPierce-and-reclaim variant (close back above centre, next-open entry):")
    for d in rows:
        if d["kind"] == "reclaim-center":
            print_study(d)


if __name__ == "__main__":
    main()
