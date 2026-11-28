"""CAKE swing lab: does a swing strategy work on 15m / 30m / 1h? (research)

One time base, three entry families plus the shipped strategies, three
timeframes. Every window is given in HOURS and converted to bars inside
prepare(), so the same grid asks the same economic question on each chart (a
2-day breakout is 192 bars at 15m, 96 at 30m, 48 at 1h) instead of silently
shrinking when the timeframe gets faster.

Two deviations from the live defaults, both required for a fair comparison:
  * the 10% account drawdown kill switch is OFF (`max_drawdown_stop=0.99`).
    Left on, it fires inside the window and flattens the rest of the test, so
    a config that halts early would be scored on fewer decisions than one
    that does not - the achieved drawdown is reported instead.
  * cost is the honest 10 bps fee + 5 bps slippage per side, 3% risk.

Protocol (docs/TARGETS.md): one causal run over the 12-month cap, split 60/40,
select on the worse of two IS halves, never on OOS. Long only.

    python research/cake/cake_swing.py
"""

from __future__ import annotations

import itertools
import math
import sys

import pandas as pd

sys.path.insert(0, "src")

from bot import indicators as ind  # noqa: E402
from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from bot.strategy.base import Action, Signal, Strategy  # noqa: E402

SYMBOL = "CAKE/USDT"
TFS = ["15m", "30m", "1h"]
BAR_HOURS = {"15m": 0.25, "30m": 0.5, "1h": 1.0}
BPY = {tf: 365.0 * 24.0 / h for tf, h in BAR_HOURS.items()}
IS_FRACTION = 0.60
MIN_IS_TRADES = 6


class SwingLab(Strategy):
    """Long-only swing entry with time-scaled windows (research probe)."""

    name = "cake_swing_lab"
    PARAMS = ("family", "lookback_hours", "trend_hours", "fast_hours", "stop_atr",
              "rr", "trail_atr", "max_hold_hours", "rsi_entry")

    def __init__(self, family: str = "breakout", lookback_hours: float = 48.0,
                 trend_hours: float = 480.0, fast_hours: float = 24.0,
                 stop_atr: float = 2.0, rr: float | None = 2.0,
                 trail_atr: float | None = None, max_hold_hours: float = 72.0,
                 rsi_entry: float = 30.0) -> None:
        if family not in ("breakout", "pullback", "dip"):
            raise ValueError("unknown family")
        super().__init__(family=family, lookback_hours=lookback_hours,
                         trend_hours=trend_hours, fast_hours=fast_hours,
                         stop_atr=stop_atr, rr=rr, trail_atr=trail_atr,
                         max_hold_hours=max_hold_hours, rsi_entry=rsi_entry)
        self.family = family
        self.lookback_hours = lookback_hours
        self.trend_hours = trend_hours
        self.fast_hours = fast_hours
        self.stop_atr = stop_atr
        self.rr = rr
        self.trail_atr = trail_atr
        self.max_hold_hours = max_hold_hours
        self.rsi_entry = rsi_entry
        self.warmup = 50
        self.hold_bars = 1
        self._entry_ts = None

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        bar_h = float(pd.Series(out["ts"]).diff().dt.total_seconds().median() / 3600.0)
        bar_h = bar_h if bar_h and bar_h > 0 else 1.0
        lb = max(2, int(round(self.lookback_hours / bar_h)))
        tr = max(5, int(round(self.trend_hours / bar_h)))
        fa = max(2, int(round(self.fast_hours / bar_h)))
        self.hold_bars = max(1, int(round(self.max_hold_hours / bar_h)))
        self.warmup = max(lb, tr, fa, 20) + 2
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        out["ema_t"] = ind.ema(out["close"], tr)
        out["ema_f"] = ind.ema(out["close"], fa)
        out["rsi"] = ind.rsi(out["close"], 14)
        out["hh"] = out["high"].rolling(lb).max().shift(1)
        out["ll"] = out["low"].rolling(lb).min().shift(1)
        return out

    def reset(self) -> None:
        self._entry_ts = None

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        if position > 0:
            ts = row["ts"]
            if self._entry_ts is None:
                self._entry_ts = ts
            pos = (df["ts"] == self._entry_ts).to_numpy().nonzero()[0]
            entry_i = int(pos[0]) if len(pos) else i
            if i - entry_i >= self.hold_bars:
                return Signal(action=Action.CLOSE, reason="time exit")
            return None

        self._entry_ts = None
        if pd.isna(row["atr"]) or pd.isna(row["ema_t"]):
            return None
        close = float(row["close"])
        prev = df.iloc[i - 1]
        hit = False
        why = ""
        if self.family == "breakout":
            hit = close > float(row["hh"]) and close > float(row["ema_t"])
            why = f"breakout over {self.lookback_hours:.0f}h high"
        elif self.family == "pullback":
            touched = float(prev["low"]) <= float(prev["ema_f"])
            reclaim = close > float(row["ema_f"]) and close > float(prev["close"])
            hit = close > float(row["ema_t"]) and touched and reclaim
            why = "pullback to fast EMA reclaimed"
        else:
            crossed = float(prev["rsi"]) < self.rsi_entry <= float(row["rsi"])
            hit = crossed and close > float(row["ema_t"])
            why = f"RSI({self.rsi_entry:.0f}) reclaim in uptrend"
        if not hit:
            return None

        stop = close - self.stop_atr * float(row["atr"])
        risk = close - stop
        if risk <= 0:
            return None
        target = close + self.rr * risk if self.rr is not None else None
        return Signal(action=Action.BUY, reason=why, stop_loss=stop,
                      take_profit=target, trail_atr=self.trail_atr)


def metrics(eq: pd.Series, pnls: list[float], bpy: float) -> dict:
    out = {"ret": float("nan"), "dd": float("nan"), "sharpe": float("nan"), "trades": 0}
    if len(eq) < 2:
        return out
    eq = eq.astype(float)
    out["ret"] = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    out["dd"] = float((eq / eq.cummax() - 1.0).min())
    r = eq.pct_change().dropna()
    sd = r.std(ddof=1)
    if len(r) > 1 and sd and sd > 0:
        out["sharpe"] = float(r.mean() / sd * math.sqrt(bpy))
    out["trades"] = len(pnls)
    return out


def risk_cfg() -> RiskConfig:
    return RiskConfig(risk_per_trade=0.03, max_position_pct=0.95,
                      max_drawdown_stop=0.99)


def run_one(strat: Strategy, bars: list, bpy: float) -> dict:
    boundary_i = int(len(bars) * IS_FRACTION)
    df = bars_to_frame(bars)
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(), risk=risk_cfg(),
                    bars_per_year=bpy)
    eng = Engine(df, strat, cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    b_ts = bars[boundary_i].ts
    m_ts = bars[boundary_i // 2].ts

    def span(lo, hi):
        return eq[(eq.index >= lo) & (eq.index < hi)]

    is_eq, h1_eq = span(eq.index[0], b_ts), span(eq.index[0], m_ts)
    h2_eq = span(m_ts, b_ts)
    oos_eq = span(b_ts, eq.index[-1] + pd.Timedelta(seconds=1))
    if len(is_eq) and len(oos_eq):
        oos_eq = pd.concat([is_eq.iloc[[-1]], oos_eq])

    def pnls(lo, hi):
        return [t.return_pct for t in res.trades if lo <= t.entry_ts < hi]

    m_is = metrics(is_eq, pnls(bars[0].ts, b_ts), bpy)
    m_h1 = metrics(h1_eq, pnls(bars[0].ts, m_ts), bpy)
    m_h2 = metrics(h2_eq, pnls(m_ts, b_ts), bpy)
    m_oos = metrics(oos_eq, pnls(b_ts, bars[-1].ts + pd.Timedelta(days=1)), bpy)
    row = {"is_ret": m_is["ret"], "is_dd": m_is["dd"], "is_trades": m_is["trades"],
           "h1_ret": m_h1["ret"], "h2_ret": m_h2["ret"],
           "oos_ret": m_oos["ret"], "oos_dd": m_oos["dd"],
           "oos_trades": m_oos["trades"],
           "full_ret": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
           "full_dd": float((eq / eq.cummax() - 1.0).min()),
           "expo": float(res.exposure.mean()),
           "halted": res.halted_reason}
    if m_is["trades"] >= MIN_IS_TRADES and not math.isnan(m_h1["ret"]) and not math.isnan(m_h2["ret"]):
        row["score"] = min(m_h1["ret"], m_h2["ret"])
    else:
        row["score"] = float("nan")
    return row


FAMILIES = ["breakout", "pullback", "dip"]
LOOKBACKS = [48.0, 120.0]
EXITS = [(2.0, None, "2R target"), (3.0, None, "3R target"),
         (None, 3.0, "trail 3 ATR"), (None, 5.0, "trail 5 ATR")]
STOPS = [2.0, 3.0]
HOLD = 72.0


def lab_grid():
    for fam, lb, stop, (rr, trail, label) in itertools.product(FAMILIES, LOOKBACKS, STOPS, EXITS):
        yield (f"{fam} {lb:.0f}h s{stop} {label}", SwingLab(
            family=fam, lookback_hours=lb, stop_atr=stop, rr=rr, trail_atr=trail,
            max_hold_hours=HOLD))


def shipped(tf: str) -> list[tuple[str, Strategy]]:
    h = BAR_HOURS[tf]
    b = lambda hours: max(3, int(round(hours / h)))  # noqa: E731
    out = []
    for window in (48.0, 120.0):
        out.append((f"donchian {window:.0f}h", build(
            "donchian", entry_window=b(window), exit_window=b(24.0), atr_mult=3.0)))
        out.append((f"momentum_swing {window:.0f}h", build(
            "momentum_swing", lookback=b(window), regime_ma=b(480.0), atr_mult=2.5,
            trail_atr=3.5, reward_risk=None)))
    out.append(("rsi_meanrev 30/480h", build(
        "rsi_meanrev", rsi_window=14, entry=30.0, exit_level=55.0,
        trend_window=b(480.0), atr_mult=2.0)))
    return out


def stage2() -> None:
    """Does a slower trend gate rescue it? (48h vs 50d vs 100d EMA)"""
    rows = []
    for tf in TFS:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=365)
        bh = bars[-1].close / bars[0].open - 1.0
        for fam, gate, stop, (rr, trail, label) in itertools.product(
                ["breakout", "dip"], [480.0, 1200.0, 2400.0], [2.0, 3.0],
                [(None, 3.0, "trail 3"), (None, 5.0, "trail 5")]):
            strat = SwingLab(family=fam, lookback_hours=120.0, trend_hours=gate,
                             stop_atr=stop, rr=rr, trail_atr=trail, max_hold_hours=HOLD)
            r = run_one(strat, bars, BPY[tf])
            r.update(tf=tf, config=f"{fam} 120h gate{gate / 24:.0f}d s{stop} {label}",
                     gate_days=gate / 24.0, bh=bh)
            rows.append(r)
        print(f"stage2 {tf} done", flush=True)
    d = pd.DataFrame(rows)
    d.to_csv("research/cake/cake_gate.csv", index=False)
    cols = ["tf", "config", "is_trades", "is_ret", "h1_ret", "h2_ret",
            "oos_trades", "oos_ret", "full_ret", "full_dd", "expo", "score"]
    for tf in TFS:
        sub = d[d.tf == tf].sort_values("full_ret", ascending=False)
        out = sub[cols].copy()
        for c in ("is_ret", "h1_ret", "h2_ret", "oos_ret", "full_ret", "full_dd", "score"):
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
        out["expo"] = out["expo"].map(lambda v: f"{v:.1%}")
        print(f"\n=== {tf} gate sweep, bh {d[d.tf == tf].bh.iloc[0]:+.1%} ===")
        print(out.to_string(index=False))
    best = d.sort_values("full_ret", ascending=False).head(5)
    print("\nbest full-window cells overall:")
    print(best[["tf", "config", "is_ret", "oos_ret", "full_ret", "full_dd",
                "is_trades", "oos_trades", "expo"]].to_string(index=False))
    print("\nwrote research/cake/cake_gate.csv", flush=True)

def main() -> None:
    rows = []
    frames = {}
    for tf in TFS:
        bars = load_scan_bars(SYMBOL, tf, source="binance", days=365)
        frames[tf] = bars
        bh = bars[-1].close / bars[0].open - 1.0
        grid = list(lab_grid()) + shipped(tf)
        for label, strat in grid:
            r = run_one(strat, bars, BPY[tf])
            r.update(tf=tf, config=label, bh=bh)
            rows.append(r)
        print(f"{tf} done ({len(grid)} configs, bh {bh:+.1%})", flush=True)
    d = pd.DataFrame(rows)
    d.to_csv("research/cake/cake_swing_tf.csv", index=False)
    cols = ["config", "is_trades", "is_ret", "h1_ret", "h2_ret",
            "oos_trades", "oos_ret", "full_ret", "full_dd", "score"]
    for tf in TFS:
        sub = d[(d.tf == tf) & d.score.notna()].sort_values("score", ascending=False).head(6)
        out = sub[cols].copy()
        for c in ("is_ret", "h1_ret", "h2_ret", "oos_ret", "full_ret", "full_dd", "score"):
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
        print(f"\n=== {tf}: top 6 by IS score (worse IS half), bh {d[d.tf == tf].bh.iloc[0]:+.1%} ===")
        print(out.to_string(index=False))
        n_ok = int(((d.tf == tf) & (d.is_ret > 0) & (d.oos_ret > 0)).sum())
        print(f"    configs with both IS and OOS positive: {n_ok} of {len(d[d.tf == tf])}")
    print("\nwrote research/cake/cake_swing_tf.csv", flush=True)


if __name__ == "__main__":
    stage = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--stage" else "1"
    stage2() if stage == "2" else main()