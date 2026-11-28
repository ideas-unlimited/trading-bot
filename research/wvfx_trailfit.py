"""Does letting the bounce run beat the hard 2.5R target? (research, 2026-11-28)

The user prefers trailing exits over a fixed profit target. That is the
documented choice for the trend book (README.md: trailing is the largest single
improvement on the gated 4h book, and `momentum_swing` ships with no hard
target). This script asks the same question of WVFX, which is mean-reversion:
keep the target and add a Chandelier trail, or drop the target and trail only.

Protocol: one causal run per symbol over the 2-year window, split 60/40,
contributions pooled across five majors, selection never sees OOS.

    python research/wvfx_trailfit.py
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "research")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import Action  # noqa: E402
from bot.strategy.wvfx_stoch import WilliamVixFixStoch  # noqa: E402

SYMS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
TF = "4h"
BPY = 2190.0
IS_FRACTION = 0.60

# the entry/exits chosen in research/WVFX_TUNE.md
BASE = dict(oversold=20, oversold_window=5, entry_window=1, trigger="cross",
            regime_days=200, stop_atr=1.5, rr=2.5, max_hold=60, exit_cross=False)


class Trail(WilliamVixFixStoch):
    """WVFX with a Chandelier trail and an optional hard target (lab probe)."""

    name = "wvfx_trailfit"
    PARAMS = WilliamVixFixStoch.PARAMS + ("trail_atr", "keep_target")

    def __init__(self, trail_atr: float = 0.0, keep_target: bool = True, **kw) -> None:
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


def one(bars: list, trail_atr: float, keep_target: bool) -> dict:
    df = bars_to_frame(bars)
    bi = int(len(bars) * IS_FRACTION)
    b_ts = bars[bi].ts
    strat = Trail(trail_atr=trail_atr, keep_target=keep_target, **BASE)
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95),
                    bars_per_year=BPY)
    eng = Engine(df, strat, cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    dd = float((eq / eq.cummax() - 1.0).min())
    # per-trade contribution = pnl / equity when the entry filled
    is_r, oos_r = [], []
    for t in res.trades:
        idx = eq.index.searchsorted(t.entry_ts)
        base = float(eq.iloc[max(0, idx - 1)])
        if base <= 0:
            continue
        c = t.pnl / base
        (is_r if t.entry_ts < b_ts else oos_r).append(c)
    hold = [ (t.exit_ts - t.entry_ts).total_seconds() / 86400.0 for t in res.trades ]
    return dict(is_r=is_r, oos_r=oos_r, full=ret, dd=dd,
                hold=sum(hold) / len(hold) if hold else 0.0)


def tstat(xs: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m / math.sqrt(var / len(xs)) if var > 0 else float("nan")


CONFIGS = [
    ("target only (current)", 0.0, True),
    ("trail 1.5 + target", 1.5, True),
    ("trail 2.5 + target", 2.5, True),
    ("trail 3.5 + target", 3.5, True),
    ("trail 1.5, no target", 1.5, False),
    ("trail 2.5, no target", 2.5, False),
    ("trail 3.5, no target", 3.5, False),
    ("trail 5.0, no target", 5.0, False),
    ("trail 7.0, no target", 7.0, False),
]


def main() -> None:
    bars = {s: load_scan_bars(s, TF, source="binance", days=730) for s in SYMS}
    rows = []
    for label, trail_atr, keep in CONFIGS:
        is_all, oos_all, fulls, holds, sol = [], [], [], [], None
        for s in SYMS:
            r = one(bars[s], trail_atr, keep)
            is_all += r["is_r"]
            oos_all += r["oos_r"]
            fulls.append(r["full"])
            holds.append(r["hold"])
            if s == "SOL/USDT":
                sol = r
        avg_is = sum(is_all) / len(is_all) if is_all else float("nan")
        avg_oos = sum(oos_all) / len(oos_all) if oos_all else float("nan")
        rows.append({
            "variant": label, "trail_atr": trail_atr, "keep_target": keep,
            "n_is": len(is_all), "avg_is": avg_is,
            "n_oos": len(oos_all), "avg_oos": avg_oos, "t_oos": tstat(oos_all),
            "med_full": float(pd.Series(fulls).median()),
            "syms_pos": int(sum(1 for f in fulls if f > 0)),
            "avg_hold_days": sum(holds) / len(holds),
            "sol_full": sol["full"], "sol_oos": sol["full"],
        })
    d = pd.DataFrame(rows).sort_values("avg_oos", ascending=False)
    out = d.copy()
    for c in ("avg_is", "avg_oos", "med_full", "sol_full"):
        out[c] = out[c].map(lambda v: f"{v:+.2%}")
    out["t_oos"] = out["t_oos"].map(lambda v: f"{v:+.2f}")
    out["avg_hold_days"] = out["avg_hold_days"].map(lambda v: f"{v:.1f}")
    print(out[["variant", "n_is", "avg_is", "n_oos", "avg_oos", "t_oos",
               "med_full", "syms_pos", "avg_hold_days", "sol_full"]].to_string(index=False))
    d.to_csv("research/wvfx_trailfit.csv", index=False)
    print("wrote research/wvfx_trailfit.csv")


if __name__ == "__main__":
    main()