"""Backtest the "volume climax = market bottom" setup from the shared video.

Loads 2 years of bars per symbol/timeframe from the Postgres cache (delta
sync, zero API calls once warm), runs the VolumeClimax strategy through the
normal engine (next-bar fills, zero fees per the user standing assumption)
and prints:

  1. headline results for the default rule on each dataset,
  2. a pooled parameter sweep,
  3. a control with the "decline + absorption" filters switched off, and
  4. an in-sample / out-of-sample split of the same rule.

Usage:  PYTHONPATH=src .venv\\Scripts\\python.exe research/vol_climax.py
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass

from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.scanlib import load_scan_bars
from bot.strategy import build

SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")
TIMEFRAMES = ("1d", "4h")
DAYS = 730

DEFAULTS = dict(vol_z=2.0, drop_pct=0.06, close_pos=0.5, rr=2.0, max_hold=20)


@dataclass
class Run:
    trades: int
    wins: int
    avg_pct: float
    pnl: float
    total_return: float
    max_dd: float
    profit_factor: float


def config(tf: str) -> BotConfig:
    return BotConfig(
        initial_cash=1000.0,
        costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),  # user standing assumption
        risk=RiskConfig(risk_per_trade=0.02, max_position_pct=0.95),
        bars_per_year=365.0 if tf == "1d" else 365.0 * 6,
    )


def run(bars, tf: str, **params) -> Run:
    res = run_backtest(bars, build("vol_climax", **params), config(tf))
    pcts = [t.return_pct for t in res.trades]
    pnl = sum(t.pnl for t in res.trades)
    wins = sum(1 for p in pcts if p > 0)
    gross_win = sum(p for p in pcts if p > 0)
    gross_loss = abs(sum(p for p in pcts if p < 0))
    m = res.metrics
    return Run(
        trades=len(pcts),
        wins=wins,
        avg_pct=(sum(pcts) / len(pcts) * 100.0) if pcts else 0.0,
        pnl=pnl,
        total_return=m.get("total_return", 0.0) * 100.0,
        max_dd=m.get("max_drawdown", 0.0) * 100.0,
        profit_factor=(gross_win / gross_loss) if gross_loss else float("inf"),
    )


def pool(runs: list[Run]) -> Run:
    trades = sum(r.trades for r in runs)
    wins = sum(r.wins for r in runs)
    weighted = sum(r.avg_pct * r.trades for r in runs)
    return Run(
        trades=trades,
        wins=wins,
        avg_pct=weighted / trades if trades else 0.0,
        pnl=sum(r.pnl for r in runs),
        total_return=sum(r.total_return for r in runs) / len(runs) if runs else 0.0,
        max_dd=min((r.max_dd for r in runs), default=0.0),
        profit_factor=statistics.fmean([r.profit_factor for r in runs]) if runs else 0.0,
    )


def fmt(r: Run) -> str:
    win = (r.wins / r.trades * 100.0) if r.trades else 0.0
    return (
        f"{r.trades:4d} trades  win {win:5.1f}%  avg {r.avg_pct:+6.2f}%  "
        f"pnl ${r.pnl:+7.1f}  ret {r.total_return:+7.1f}%  dd {r.max_dd:6.1f}%"
    )


def main() -> None:
    data: dict[tuple[str, str], list] = {}
    print("== data (2-year window, Postgres cache)")
    for sym, tf in itertools.product(SYMBOLS, TIMEFRAMES):
        bars = load_scan_bars(sym, tf, days=DAYS)
        data[(sym, tf)] = bars
        print(f"  {sym:8s} {tf:2s} {len(bars):5d} bars  {bars[0].ts.date()} -> {bars[-1].ts.date()}")

    print("\n== headline: defaults " + str(DEFAULTS))
    default_runs = []
    for (sym, tf), bars in data.items():
        r = run(bars, tf, **DEFAULTS)
        default_runs.append(r)
        print(f"  {sym:8s} {tf:2s} " + fmt(r))
    print("  " + "-" * 84)
    print("  POOLED     " + fmt(pool(default_runs)))

    print("\n== sweep (pooled over all 6 datasets, ranked by avg trade %)")
    grid = dict(
        vol_z=(1.5, 2.0, 2.5),
        drop_pct=(0.0, 0.05, 0.08),
        close_pos=(0.35, 0.5),
        rr=(1.5, 2.0, 3.0),
    )
    cells = []
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(DEFAULTS)
        params.update(dict(zip(keys, combo)))
        runs = [run(bars, tf, **params) for (sym, tf), bars in data.items()]
        cells.append((pool(runs), params))
    cells.sort(key=lambda c: c[0].avg_pct, reverse=True)
    for pooled, params in cells[:12]:
        tag = "  ".join(f"{k}={params[k]}" for k in keys)
        print(f"  {tag:52s} " + fmt(pooled))

    print("\n== control: pure volume spike (no decline / absorption filter)")
    for z in (1.5, 2.0, 2.5, 3.0):
        params = dict(DEFAULTS, vol_z=z, drop_pct=0.0, close_pos=0.0)
        runs = [run(bars, tf, **params) for (sym, tf), bars in data.items()]
        print(f"  z>={z:3.1f}  " + fmt(pool(runs)))
    print("  (compare with the same z under the full rule)")
    for z in (1.5, 2.0, 2.5, 3.0):
        params = dict(DEFAULTS, vol_z=z)
        runs = [run(bars, tf, **params) for (sym, tf), bars in data.items()]
        print(f"  z>={z:3.1f}  " + fmt(pool(runs)))

    print("\n== in-sample (first 70% of bars) vs out-of-sample (last 30%)")
    is_runs, oos_runs = [], []
    for (sym, tf), bars in data.items():
        cut = int(len(bars) * 0.7)
        is_runs.append(run(bars[:cut], tf, **DEFAULTS))
        oos_runs.append(run(bars[cut:], tf, **DEFAULTS))
    print("  IS   " + fmt(pool(is_runs)))
    print("  OOS  " + fmt(pool(oos_runs)))


if __name__ == "__main__":
    main()
