"""Throwaway research: find the strongest SOL/USD strategy (1h/4h/1d).

Method (honest, no lookahead):
- real bars from the Postgres cache (ingested from Alpaca)
- every backtest runs through the project engine: signals on bar i fill at
  bar i+1 open, fees on both sides, adverse slippage, stop-aware sizing
- costs set to Alpaca crypto reality: 25 bps fee + 5 bps slippage per side
- walk-forward: rank on the first 70% of bars (in-sample), then evaluate
  the survivors on the final 30% (out-of-sample). OOS is what we report.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bot import db as store
from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.data.feed import Bar
from bot.strategy import REGISTRY, build
from bot.strategy.base import Action, Signal, Strategy

FEE_BPS = 25.0
SLIPPAGE_BPS = 5.0
IS_FRACTION = 0.7


class DonchianBreakout(Strategy):
    """Buy N-bar-high breakouts, exit on M-bar-low breakdown, ATR stop."""

    name = "donchian"
    PARAMS = ("entry_window", "exit_window", "atr_mult")

    def __init__(self, entry_window: int = 55, exit_window: int = 20, atr_mult: float = 3.0) -> None:
        super().__init__(entry_window=entry_window, exit_window=exit_window, atr_mult=atr_mult)
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.atr_mult = atr_mult
        self.warmup = max(entry_window, exit_window, 14) + 1

    def prepare(self, df):
        import pandas as pd
        out = df.copy()
        out["entry_level"] = out["high"].rolling(self.entry_window).max().shift(1)
        out["exit_level"] = out["low"].rolling(self.exit_window).min().shift(1)
        out["atr"] = out["high"].sub(out["low"]).combine(
            (out["high"] - out["close"].shift()).abs(), max
        ).combine((out["low"] - out["close"].shift()).abs(), max).rolling(14).mean()
        return out

    def signal(self, i, df, position):
        row = df.iloc[i]
        if row["entry_level"] != row["entry_level"] or row["atr"] != row["atr"]:
            return None
        if position == 0 and row["close"] > row["entry_level"]:
            return Signal(Action.BUY, reason=f"breakout > {self.entry_window}d high",
                          stop_loss=float(row["close"] - self.atr_mult * row["atr"]))
        if position > 0 and row["exit_level"] == row["exit_level"] and row["close"] < row["exit_level"]:
            return Signal(Action.CLOSE, reason=f"breakdown < {self.exit_window}d low")
        return None


def make_config(bars):
    from bot.metrics import infer_bars_per_year
    return BotConfig(
        initial_cash=10_000.0,
        costs=CostConfig(fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS),
        risk=RiskConfig(risk_per_trade=0.01, max_position_pct=0.95, max_drawdown_stop=0.25),
        bars_per_year=infer_bars_per_year([b.ts for b in bars]),
    )


def grid():
    """(family, param-set) combinations."""
    out = []
    for fast in (10, 20, 30, 50):
        for slow in (100, 150, 200):
            out.append(("sma_cross", {"fast": fast, "slow": slow, "atr_mult": 2.5}))
    for entry in (25, 30, 35):
        for exit_ in (55, 60, 65):
            out.append(("rsi_meanrev", {"entry": entry, "exit_level": exit_, "trend_window": 200, "atr_mult": 2.0}))
    for entry_w in (20, 55):
        for exit_w in (10, 20):
            out.append(("donchian", {"entry_window": entry_w, "exit_window": exit_w, "atr_mult": 3.0}))
    return out


def evaluate(bars, family, params):
    strategy = build(family, **params) if family in REGISTRY else DonchianBreakout(**params)
    result = run_backtest(bars, strategy, make_config(bars))
    m = result.metrics
    return {
        "return": m.get("total_return", float("nan")),
        "sharpe": m.get("sharpe", float("nan")),
        "maxdd": m.get("max_drawdown", float("nan")),
        "trades": int(m.get("trades") or 0),
        "win": m.get("win_rate", float("nan")),
        "exposure": m.get("exposure", float("nan")),
    }


def main():
    conn = store.connect()
    bnh_summary = []
    for tf in ("1h", "4h", "1d"):
        bars = store.load_bars(conn, "SOL/USD", tf)
        if len(bars) < 300:
            print(f"[{tf}] only {len(bars)} bars; skipping"); continue
        split = int(len(bars) * IS_FRACTION)
        is_bars, oos_bars = bars[:split], bars[split:]

        # buy & hold baselines
        bh_is = is_bars[-1].close / is_bars[0].close - 1
        bh_oos = oos_bars[-1].close / oos_bars[0].close - 1
        bnh_summary.append((tf, len(bars), bh_is, bh_oos))

        results = []
        for family, params in grid():
            try:
                r = evaluate(is_bars, family, params)
            except Exception as exc:
                continue
            r.update(tf=tf, family=family, params=params)
            results.append(r)

        valid = [r for r in results if r["trades"] >= 5 and r["sharpe"] == r["sharpe"]]
        valid.sort(key=lambda r: r["sharpe"], reverse=True)
        top = valid[:6]

        print(f"\n===== SOL/USD {tf} | {len(bars)} bars | B&H: IS {bh_is:+.1%}, OOS {bh_oos:+.1%} =====")
        print(f"{'family':<12} {'params':<46} {'IS ret':>8} {'IS shp':>7} {'IS trd':>6}  | {'OOS ret':>8} {'OOS shp':>7} {'OOS dd':>7} {'OOS trd':>6}")
        for r in top:
            try:
                o = evaluate(oos_bars, r["family"], r["params"])
                oos = (f"{o['return']:+8.1%} {o['sharpe']:7.2f} {o['maxdd']:+7.1%} {o['trades']:6d}")
            except Exception:
                oos = "eval failed"
            p = ", ".join(f"{k}={v}" for k, v in r["params"].items())
            print(f"{r['family']:<12} {p:<46} {r['return']:+8.1%} {r['sharpe']:7.2f} {r['trades']:6d}  | {oos}")

    print("\n===== buy & hold baseline =====")
    for tf, n, bh_is, bh_oos in bnh_summary:
        print(f"{tf:>4}: {n:>6} bars  IS {bh_is:+.1%}  OOS {bh_oos:+.1%}")


if __name__ == "__main__":
    main()
