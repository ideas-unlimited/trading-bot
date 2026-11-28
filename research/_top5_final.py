"""Final trade-level audit of the five selected sleeves (scratch)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE / "cake"))
sys.path.insert(0, str(HERE))

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from cake_swing import SwingLab  # noqa: E402

RISK = 0.03
INIT = 1000.0
COSTS = CostConfig(fee_bps=10.0, slippage_bps=5.0)
BAR_H = {"4h": 4.0, "1d": 24.0}


def make():
    return [
        ("SUI/USDT", "4h", "SUI breakout 240h s2 t2.5",
         SwingLab(family="breakout", lookback_hours=240, trend_hours=480,
                  stop_atr=2.0, rr=None, trail_atr=2.5, max_hold_hours=72)),
        ("XRP/USDT", "4h", "XRP WVF s2 2R 200d",
         build("wvfx_stoch", stop_atr=2.0, rr=2.0, max_hold=240,
               regime_days=200.0)),
        ("TAO/USDT", "1d", "TAO breakout 120h s2 t3",
         SwingLab(family="breakout", lookback_hours=120, trend_hours=480,
                  stop_atr=2.0, rr=None, trail_atr=3.0, max_hold_hours=72)),
        ("INJ/USDT", "1d", "INJ breakout 168h s1.75 t3",
         SwingLab(family="breakout", lookback_hours=168, trend_hours=480,
                  stop_atr=1.75, rr=None, trail_atr=3.0, max_hold_hours=72)),
        ("AVAX/USDT", "1d", "AVAX breakout 288h s2 t3",
         SwingLab(family="breakout", lookback_hours=288, trend_hours=480,
                  stop_atr=2.0, rr=None, trail_atr=3.0, max_hold_hours=72)),
    ]


def metrics(eq: pd.Series, trades, start, end) -> dict:
    seg = eq[(eq.index >= start) & (eq.index <= end)]
    tr = [t for t in trades if start <= t.exit_ts <= end]
    pnl = np.array([t.pnl for t in tr], dtype=float)
    wins = int((pnl > 0).sum())
    gp = float(pnl[pnl > 0].sum())
    gl = float(-pnl[pnl < 0].sum())
    biggest = np.sort(pnl)[::-1] if len(pnl) else np.array([])
    top3 = float(biggest[:3].sum()) if len(pnl) else 0.0
    streak = 0
    worst_streak = 0
    for x in pnl:
        if x < 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0
    holds = [max(0.0, (t.exit_ts - t.entry_ts).total_seconds() / 3600.0) for t in tr]
    return {
        "start": str(seg.index[0]), "end": str(seg.index[-1]),
        "ret": float(seg.iloc[-1] / seg.iloc[0] - 1.0),
        "dd": float((seg / seg.cummax() - 1.0).min()),
        "trades": len(tr), "wins": wins,
        "win_rate": wins / len(tr) if tr else 0.0,
        "pf": gp / gl if gl else float("inf"),
        "avg_trade_pct": float(np.mean([t.return_pct for t in tr])) if tr else 0.0,
        "avg_r": float(np.mean(pnl) / (INIT * RISK)) if tr else 0.0,
        "expectancy_pct": float(np.mean(pnl) / INIT) if tr else 0.0,
        "best": float(biggest[0]) if len(pnl) else 0.0,
        "worst": float(biggest[-1]) if len(pnl) else 0.0,
        "top3_share": top3 / float(pnl.sum()) if len(pnl) and pnl.sum() > 0 else float("nan"),
        "max_consec_losses": worst_streak,
        "avg_hold_hours": float(np.mean(holds)) if holds else 0.0,
    }


def bootstrap(pnl: np.ndarray, n: int = 5000):
    if len(pnl) < 5:
        return {}
    rng = np.random.default_rng(7)
    exp = []
    pf = []
    for _ in range(n):
        s = rng.choice(pnl, len(pnl), replace=True)
        gp = s[s > 0].sum()
        gl = -s[s < 0].sum()
        exp.append(s.mean())
        pf.append(gp / gl if gl else np.nan)
    return {
        "exp_mean": float(np.mean(exp)),
        "exp_lo": float(np.percentile(exp, 2.5)),
        "exp_hi": float(np.percentile(exp, 97.5)),
        "pf_lo": float(np.nanpercentile(pf, 2.5)),
        "pf_hi": float(np.nanpercentile(pf, 97.5)),
    }


def main():
    out = []
    for sym, tf, label, strat in make():
        bars = load_scan_bars(sym, tf, source="binance", days=740)
        df = bars_to_frame(bars)
        cfg = BotConfig(initial_cash=INIT, costs=COSTS,
                        risk=RiskConfig(risk_per_trade=RISK, max_position_pct=0.95,
                                        max_drawdown_stop=0.99),
                        bars_per_year=365.0 * 24.0 / BAR_H[tf])
        eng = Engine(df, strat, cfg)
        eng.run()
        res = eng.result()
        eq = res.equity_curve
        last = df["ts"].iloc[-1]
        split = last - pd.Timedelta(days=365)
        start = last - pd.Timedelta(days=730)
        first = max(df["ts"].iloc[0], start)
        rec = {
            "symbol": sym, "tf": tf, "label": label,
            "bars": int(len(df)), "first": str(df["ts"].iloc[0]), "last": str(last),
            "train": metrics(eq, res.trades, first, split),
            "test": metrics(eq, res.trades, split, last),
            "full": metrics(eq, res.trades, first, last),
            "exposure_mean": float(res.exposure.mean()),
            "bootstrap_full": bootstrap(np.array([t.pnl for t in res.trades])),
        }
        out.append(rec)
        print(f"\n=== {label} [{tf}] bars={len(df)} ===")
        for k in ("train", "test", "full"):
            m = rec[k]
            print(f"{k:5s} {m['start'][:10]}..{m['end'][:10]} "
                  f"ret {m['ret']:+.2%} dd {m['dd']:.2%} n {m['trades']} "
                  f"win {m['win_rate']:.1%} pf {m['pf']:.2f} avgR {m['avg_r']:+.2f} "
                  f"top3 {m['top3_share']:.0%} streak {m['max_consec_losses']} "
                  f"hold {m['avg_hold_hours']:.0f}h")
        print(f"exposure {rec['exposure_mean']:.1%} bootstrap {rec['bootstrap_full']}")
        eq.to_csv(HERE / f"final_{sym.split('/')[0]}_{tf}_equity.csv")
        pd.DataFrame([{
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "qty": t.qty,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "fees": t.fees, "pnl": t.pnl, "return_pct": t.return_pct,
            "reason_open": t.reason_open, "reason_close": t.reason_close,
        } for t in res.trades]).to_csv(HERE / f"final_{sym.split('/')[0]}_{tf}_trades.csv", index=False)
    (HERE / "top5_final.json").write_text(json.dumps(out, indent=2, default=str))
    print("\nwrote top5_final.json")


if __name__ == "__main__":
    main()
