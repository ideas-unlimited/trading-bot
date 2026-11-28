"""Broad 1-year sweep: every tracked coin x every strategy x tuned params.

One year of bars (user rule for this study: keep it real and quick), 4h and 1d
first because they are the house charts and the cheapest to run. Every window
is expressed in HOURS and converted to bars per chart, so the same cell asks
the same question at 4h and at 1d.

Protocol: signal on the close, fill next open, 10 bps + 5 bps per side, 3%
risk, $1,000 start, long only. The 10% account drawdown switch is off so a
config cannot be scored on a window it was flattened out of. IS = first 60% of
the year, OOS = last 40%; the ranking score is min(the two IS halves) and is
never computed from OOS.

    python research/allcoins_sweep.py            # stage 1: 4h + 1d, all coins
    python research/allcoins_sweep.py --tf 6h 1h # stage 2: shortlist charts
"""

from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path

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
    from cake_swing import SwingLab  # lab families, same time base
except Exception:  # pragma: no cover - lab import is optional
    SwingLab = None

WINDOW_DAYS = 365
LOAD_DAYS = 455  # extra 90 days so warmup burns outside the measured year
IS_FRACTION = 0.60
MIN_TRADES = 8
BAR_HOURS = {"1h": 1.0, "4h": 4.0, "6h": 6.0, "12h": 12.0, "1d": 24.0}


def universe() -> list[str]:
    cfg = json.loads((ROOT / "config" / "target_symbols.json").read_text(encoding="utf-8"))
    tracked = [s["symbol"] for s in cfg["symbols"]]
    extra = ["ADA/USDT", "APT/USDT", "ARB/USDT", "ATOM/USDT", "AVAX/USDT", "BCH/USDT",
             "BTC/USDT", "DOGE/USDT", "DOT/USDT", "LTC/USDT", "NEAR/USDT", "OP/USDT",
             "TRX/USDT", "ETH/USDT"]
    out = []
    for s in tracked + extra:
        if s not in out:
            out.append(s)
    return out


def grids(tf: str) -> list[tuple[str, object]]:
    h = BAR_HOURS[tf]

    def b(hours: float) -> int:
        return max(1, int(round(hours / h)))

    out: list[tuple[str, object]] = []
    for fast, slow in ((24, 96), (48, 192), (96, 384)):
        out.append((f"sma_cross {fast:.0f}/{slow:.0f}h",
                    build("sma_cross", fast=b(fast), slow=b(slow), atr_mult=2.5)))
    for entry, exit_ in ((48, 24), (120, 48), (240, 96)):
        e_b = b(entry)
        x_b = max(1, min(b(exit_), e_b - 1))
        out.append((f"donchian {entry:.0f}/{exit_:.0f}h",
                    build("donchian", entry_window=e_b, exit_window=x_b,
                          atr_mult=3.0)))
    out.append(("rsi_meanrev 30/480h",
                build("rsi_meanrev", rsi_window=14, entry=30.0, exit_level=55.0,
                      trend_window=b(480), atr_mult=2.0)))
    out.append(("rsi_meanrev 25/960h",
                build("rsi_meanrev", rsi_window=14, entry=25.0, exit_level=55.0,
                      trend_window=b(960), atr_mult=2.0)))
    out.append(("rsi_meanrev 35/480h",
                build("rsi_meanrev", rsi_window=14, entry=35.0, exit_level=60.0,
                      trend_window=b(480), atr_mult=2.0)))
    for look, reg, trail in ((120, 480, 3.5), (240, 960, 3.5), (120, 480, 5.0)):
        out.append((f"momentum_swing {look:.0f}/{reg:.0f}h t{trail}",
                    build("momentum_swing", lookback=b(look), regime_ma=b(reg),
                          atr_mult=2.5, trail_atr=trail, reward_risk=None, breakout=0.02)))
    out.append(("capdip 240h d20 rr1.5",
                build("capitulation_dip_uni", window=b(240), drop_pct=0.20, stop_atr=2.5,
                      rr=1.5, max_hold=b(72))))
    out.append(("capdip 240h d30 rr2.5",
                build("capitulation_dip_uni", window=b(240), drop_pct=0.30, stop_atr=2.5,
                      rr=2.5, max_hold=b(120))))
    out.append(("capdip 240h d20 trail5",
                build("capitulation_dip_uni", window=b(240), drop_pct=0.20, stop_atr=2.5,
                      rr=None, max_hold=b(120), trail_atr=5.0)))
    out.append(("vol_climax z2 d6 hold48h",
                build("vol_climax", vol_window=b(80), vol_z=2.0, lookback=b(80),
                      drop_pct=0.06, close_pos=0.5, rr=2.0, stop_atr=0.5,
                      max_hold=b(48))))
    out.append(("vol_climax z3 d10 hold72h",
                build("vol_climax", vol_window=b(80), vol_z=3.0, lookback=b(80),
                      drop_pct=0.10, close_pos=0.5, rr=2.5, stop_atr=0.5,
                      max_hold=b(72))))
    out.append(("wvfx_stoch 1.5/2.5 200d",
                build("wvfx_stoch", stop_atr=1.5, rr=2.5, max_hold=b(240), regime_days=200.0)))
    out.append(("wvfx_stoch 2.0/2.0 100d",
                build("wvfx_stoch", stop_atr=2.0, rr=2.0, max_hold=b(120), regime_days=100.0)))
    out.append(("wvfx_stoch 1.5/2.5 100d",
                build("wvfx_stoch", stop_atr=1.5, rr=2.5, max_hold=b(240), regime_days=100.0)))
    if SwingLab is not None:
        out.append(("lab breakout 120h s2 t3",
                    SwingLab(family="breakout", lookback_hours=120, stop_atr=2.0,
                             rr=None, trail_atr=3.0, max_hold_hours=72)))
        out.append(("lab breakout 120h s3 t5",
                    SwingLab(family="breakout", lookback_hours=120, stop_atr=3.0,
                             rr=None, trail_atr=5.0, max_hold_hours=72)))
        out.append(("lab pullback 120h s2 2R",
                    SwingLab(family="pullback", lookback_hours=120, stop_atr=2.0,
                             rr=2.0, max_hold_hours=72)))
        out.append(("lab dip 120h s3 t5 g50d",
                    SwingLab(family="dip", lookback_hours=120, trend_hours=1200,
                             stop_atr=3.0, rr=None, trail_atr=5.0, max_hold_hours=72)))
        out.append(("lab dip 120h s2 2R g50d",
                    SwingLab(family="dip", lookback_hours=120, trend_hours=1200,
                             stop_atr=2.0, rr=2.0, max_hold_hours=72)))
        out.append(("lab dip 240h s3 t5 g100d",
                    SwingLab(family="dip", lookback_hours=240, trend_hours=2400,
                             stop_atr=3.0, rr=None, trail_atr=5.0, max_hold_hours=96)))
    return out


def metrics(eq: pd.Series, rets: list[float], bpy: float) -> dict:
    out = {"ret": float("nan"), "dd": float("nan"), "trades": 0, "wins": 0,
           "avg": float("nan"), "pnl": 0.0}
    if len(eq) < 2:
        return out
    eq = eq.astype(float)
    out["ret"] = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    out["dd"] = float((eq / eq.cummax() - 1.0).min())
    out["trades"] = len(rets)
    out["wins"] = sum(1 for r in rets if r > 0)
    out["avg"] = float(sum(rets) / len(rets)) if rets else float("nan")
    out["pnl"] = float(eq.iloc[-1] - eq.iloc[0])
    return out


def run_one(strat, df: pd.DataFrame, bpy: float, start_ts) -> dict:
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95,
                                    max_drawdown_stop=0.99),
                    bars_per_year=bpy)
    eng = Engine(df, strat, cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    eq = eq[eq.index >= start_ts]
    if len(eq) < 2:
        return {"ret": float("nan"), "dd": float("nan"), "trades": 0, "wins": 0,
                "avg": float("nan"), "pnl": 0.0, "is_ret": float("nan"),
                "oos_ret": float("nan"), "is_trades": 0, "score": float("nan"),
                "expo": 0.0}
    base = float(eq.iloc[0])
    tr = [t for t in res.trades if t.entry_ts >= start_ts]
    mid = eq.index[0] + (eq.index[-1] - eq.index[0]) * IS_FRACTION
    is_eq = eq[eq.index < mid]
    oos_eq = eq[eq.index >= mid]
    if len(is_eq) and len(oos_eq):
        oos_eq = pd.concat([is_eq.iloc[[-1]], oos_eq])
    is_tr = [t for t in tr if t.entry_ts < mid]
    is_rets = [t.return_pct for t in is_tr]
    m = metrics(eq / base * 1000.0, [t.return_pct for t in tr], bpy)
    m["is_ret"] = (is_eq.iloc[-1] / is_eq.iloc[0] - 1.0) if len(is_eq) > 1 else float("nan")
    m["oos_ret"] = (oos_eq.iloc[-1] / oos_eq.iloc[0] - 1.0) if len(oos_eq) > 1 else float("nan")
    m["is_trades"] = len(is_tr)
    m["expo"] = float(res.exposure[res.exposure.index >= start_ts].mean()) if len(res.exposure) else 0.0
    if len(is_rets) >= 4:
        half = len(is_eq) // 2
        m["score"] = min(float(is_eq.iloc[:half].iloc[-1] / is_eq.iloc[0] - 1.0) if half else float("nan"),
                         float(is_eq.iloc[-1] / is_eq.iloc[half] - 1.0) if half else float("nan"))
    else:
        m["score"] = float("nan")
    return m


def main() -> None:
    tfs = sys.argv[2:] if len(sys.argv) > 2 and sys.argv[1] == "--tf" else ["4h", "1d"]
    rows = []
    skipped = []
    syms = universe()
    print(f"{len(syms)} symbols, charts {tfs}", flush=True)
    for tf in tfs:
        bpy = 365.0 * 24.0 / BAR_HOURS[tf]
        for sym in syms:
            try:
                bars = load_scan_bars(sym, tf, source="binance", days=LOAD_DAYS)
            except Exception as exc:
                skipped.append((sym, tf, type(exc).__name__, str(exc)[:120]))
                continue
            if len(bars) < 200:
                skipped.append((sym, tf, "short", f"{len(bars)} bars"))
                continue
            df = bars_to_frame(bars)
            last = df["ts"].iloc[-1]
            start_ts = last - pd.Timedelta(days=WINDOW_DAYS)
            bh = float(df.loc[df["ts"] >= start_ts, "close"].iloc[-1] /
                       df.loc[df["ts"] >= start_ts, "open"].iloc[0] - 1.0)
            built = []
            for label, strat in grids(tf):
                built.append((label, strat))
            for label, strat in built:
                try:
                    if hasattr(strat, "reset"):
                        strat.reset()
                    m = run_one(strat, df, bpy, start_ts)
                except Exception:
                    print(f"  !! {tf} {sym} {label}", flush=True)
                    traceback.print_exc()
                    continue
                m.update(symbol=sym, tf=tf, config=label, bh=bh)
                rows.append(m)
            pd.DataFrame(rows).to_csv(HERE / "allcoins_stage1.csv", index=False)
            print(f"  {tf} {sym} {len(built)} cells", flush=True)
    d = pd.DataFrame(rows)
    out = HERE / "allcoins_stage1.csv"
    d.to_csv(out, index=False)
    print(f"\nwrote {out}  rows={len(d)}")
    if skipped:
        print("skipped:", skipped)
    ok = d[(d.trades >= MIN_TRADES) & d.ret.notna()]
    cols = ["symbol", "tf", "config", "ret", "dd", "trades", "wins", "is_ret",
            "oos_ret", "score", "bh"]
    show = ok.sort_values("ret", ascending=False).head(20)[cols].copy()
    for c in ("ret", "dd", "is_ret", "oos_ret", "score", "bh"):
        show[c] = show[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    print("\n=== top 20 by 1-year return (>=8 trades) ===")
    print(show.to_string(index=False))
    show2 = ok.sort_values("score", ascending=False).head(20)[cols].copy()
    for c in ("ret", "dd", "is_ret", "oos_ret", "score", "bh"):
        show2[c] = show2[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    print("\n=== top 20 by IS score ===")
    print(show2.to_string(index=False))


if __name__ == "__main__":
    main()