"""Re-run the five top-5 sleeves through Engine and write promotion evidence.

The classes are the shipped ones, not the research harness, so this is the
check that what is promoted is what was measured.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.promotions import params_hash  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "top5" / "evidence"
INIT = 1000.0
RISK = 0.03
COSTS = CostConfig(fee_bps=10.0, slippage_bps=5.0)

CASES = [
    ("swing_breakout_sui", "SUI/USDT", "4h", 61.34, -13.14, 30),
    ("wvf_stoch_xrp", "XRP/USDT", "4h", 48.92, -7.29, 19),
    ("swing_breakout_inj", "INJ/USDT", "1d", 23.46, -5.34, 27),
    ("swing_breakout_tao", "TAO/USDT", "1d", 21.64, -5.61, 32),
    ("swing_breakout_avax", "AVAX/USDT", "1d", 13.33, -6.05, 20),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    failures = []
    for name, symbol, tf, exp_ret, exp_dd, exp_trades in CASES:
        strat = build(name)
        digest = params_hash(strat.params)
        bars = load_scan_bars(symbol, tf, source="binance", days=730)
        df = bars_to_frame(bars)
        cfg = BotConfig(
            initial_cash=INIT,
            costs=COSTS,
            risk=RiskConfig(
                risk_per_trade=RISK,
                max_position_pct=0.95,
                max_drawdown_stop=0.99,
            ),
            bars_per_year=365.0 * 24.0 / {"4h": 4.0, "1d": 24.0}[tf],
        )
        eng = Engine(df, strat, cfg)
        eng.run()
        res = eng.result()
        eq = res.equity_curve
        pnl = np.array([t.pnl for t in res.trades], dtype=float)
        gp = float(pnl[pnl > 0].sum())
        gl = float(-pnl[pnl < 0].sum())
        ret = float(eq.iloc[-1] / INIT - 1.0)
        dd = float((eq / eq.cummax() - 1.0).min())
        wins = int((pnl > 0).sum())
        first = pd.Timestamp(df["ts"].iloc[0])
        last = pd.Timestamp(df["ts"].iloc[-1])
        evidence = {
            "symbol": symbol,
            "timeframe": tf,
            "window": f"{first.date()} .. {last.date()}",
            "bars": int(len(df)),
            "window_days": int((last - first).total_seconds() // 86400),
            "trades": len(res.trades),
            "fee_bps": 10.0,
            "slippage_bps": 5.0,
            "return_pct": round(ret, 6),
            "max_drawdown": round(dd, 6),
            "win_rate": round(wins / len(res.trades), 6) if res.trades else 0.0,
            "profit_factor": round(gp / gl, 4) if gl else None,
            "params_hash": digest,
            "command": f"python research/top5_promote.py  # {name} on {symbol}, {tf}, two years",
            "note": (
                "shipped sleeve re-run through Engine: next-open fills, stop-first "
                "intrabar, 3% risk, 95% max position, drawdown switch off, "
                "10 bps fee + 5 bps slippage a side; strict two-year load (days=730)"
            ),
            "risk_per_trade": RISK,
            "max_position_pct": 0.95,
            "max_drawdown_stop": 0.99,
        }
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        ok = (
            abs(ret * 100.0 - exp_ret) < 0.15
            and abs(dd * 100.0 - exp_dd) < 0.15
            and len(res.trades) == exp_trades
        )
        print(
            f"{name:22s} {symbol:9s} {tf:2s} ret {ret:+7.2%} "
            f"(exp {exp_ret:+.2f}%) dd {dd:7.2%} (exp {exp_dd:.2f}%) "
            f"n {len(res.trades):2d} (exp {exp_trades}) {'OK' if ok else 'MISMATCH'}"
        )
        if not ok:
            failures.append(name)
    print(f"params_hash {digest}")
    if failures:
        raise SystemExit(f"mismatched sleeves: {failures}")


if __name__ == "__main__":
    main()
