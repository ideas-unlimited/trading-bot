"""Two-year train/validate scan for new per-coin spot sleeves (scratch)."""
from __future__ import annotations

import json
import sys
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
from cake_swing import SwingLab  # noqa: E402

TFS = ["1d"]
BAR_HOURS = {"4h": 4.0, "6h": 6.0, "12h": 12.0, "1d": 24.0, "1h": 1.0}
MIN_TRAIN_TRADES = 6
MIN_TRAIN_BARS = 250
SPLIT_DAYS = 365
LOAD_DAYS = 740


def universe() -> list[str]:
    cfg = json.loads((ROOT / "config" / "target_symbols.json").read_text(encoding="utf-8"))
    return [s["symbol"] for s in cfg["symbols"]]


def grid(tf: str):
    h = BAR_HOURS[tf]

    def b(hours: float) -> int:
        return max(1, int(round(hours / h)))

    out = []
    for look in (120, 240):
        for stop in (2.0, 3.0):
            for trail in (3.0, 5.0):
                out.append((f"lab breakout {look}h s{stop:g} t{trail:g}",
                            SwingLab(family="breakout", lookback_hours=look,
                                     stop_atr=stop, rr=None, trail_atr=trail,
                                     max_hold_hours=72)))
    for look in (120, 240):
        for stop in (2.0, 2.5):
            for rr in (2.0, 3.0):
                out.append((f"lab pullback {look}h s{stop:g} {rr:g}R",
                            SwingLab(family="pullback", lookback_hours=look,
                                     stop_atr=stop, rr=rr, max_hold_hours=72)))
    for look in (120, 240):
        for stop in (2.0, 3.0):
            out.append((f"lab dip {look}h s{stop:g} t5",
                        SwingLab(family="dip", lookback_hours=look,
                                 trend_hours=1200, stop_atr=stop, rr=None,
                                 trail_atr=5.0, max_hold_hours=72)))
    for look in (120, 240):
        for trail in (3.5, 5.0):
            out.append((f"momentum {look}h t{trail:g}",
                        build("momentum_swing", lookback=b(look), regime_ma=b(480),
                              atr_mult=2.5, trail_atr=trail)))
    for entry in (120, 240):
        out.append((f"donchian {entry}h/48h",
                    build("donchian", entry_window=b(entry), exit_window=b(48),
                          atr_mult=3.0)))
    for drop in (0.15, 0.20, 0.25):
        for stop in (2.0, 2.5):
            out.append((f"capdip 240h d{drop:.0%} s{stop:g} rr1.5",
                        build("capitulation_dip_uni", window=b(240), drop_pct=drop,
                              stop_atr=stop, rr=1.5, max_hold=b(120))))
    for z in (2.0, 3.0):
        for rr in (2.0, 2.5):
            out.append((f"volclimax z{z:g} rr{rr:g}",
                        build("vol_climax", vol_window=b(80), vol_z=z,
                              lookback=b(80), drop_pct=0.06, close_pos=0.5,
                              rr=rr, stop_atr=0.5, max_hold=b(48))))
    for stop in (1.5, 2.0):
        for rr in (2.0, 2.5):
            out.append((f"wvf_stoch s{stop:g} {rr:g}R",
                        build("wvfx_stoch", stop_atr=stop, rr=rr,
                              max_hold=b(240), regime_days=200.0)))
    for entry in (25.0, 30.0):
        out.append((f"rsi {entry:g}/480h",
                    build("rsi_meanrev", rsi_window=14, entry=entry,
                          exit_level=55.0, trend_window=b(480), atr_mult=2.0)))
    return out


def stats(eq: pd.Series, trades, start, end) -> dict:
    seg = eq[(eq.index >= start) & (eq.index <= end)]
    if len(seg) < 2:
        return {"ret": float("nan"), "dd": float("nan"), "trades": 0,
                "wins": 0, "pf": float("nan"), "expo": 0.0}
    tr = [t for t in trades if start <= t.entry_ts <= end]
    rets = [t.pnl for t in tr]
    wins = sum(1 for x in rets if x > 0)
    gp = sum(x for x in rets if x > 0)
    gl = -sum(x for x in rets if x < 0)
    return {
        "ret": float(seg.iloc[-1] / seg.iloc[0] - 1.0),
        "dd": float((seg / seg.cummax() - 1.0).min()),
        "trades": len(tr),
        "wins": wins,
        "pf": gp / gl if gl else float("nan"),
    }


def run(sym: str, tf: str, label: str, strat, bars) -> dict | None:
    df = bars_to_frame(bars)
    if len(df) < 300:
        return None
    last = df["ts"].iloc[-1]
    split = last - pd.Timedelta(days=SPLIT_DAYS)
    start = last - pd.Timedelta(days=2 * SPLIT_DAYS)
    if df["ts"].iloc[0] > start + pd.Timedelta(days=60):
        return None
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95,
                                    max_drawdown_stop=0.99),
                    bars_per_year=365.0 * 24.0 / BAR_HOURS[tf])
    eng = Engine(df, strat, cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    train = stats(eq, res.trades, start, split)
    if train["trades"] < MIN_TRAIN_TRADES:
        return None
    mid = start + (split - start) / 2
    h1 = stats(eq, res.trades, start, mid)
    h2 = stats(eq, res.trades, mid, split)
    test = stats(eq, res.trades, split, last)
    full = stats(eq, res.trades, start, last)
    score = min(h1["ret"], h2["ret"]) / max(0.02, abs(train["dd"]))
    return {
        "symbol": sym, "tf": tf, "config": label,
        "train_ret": train["ret"], "train_dd": train["dd"],
        "train_trades": train["trades"], "train_wins": train["wins"],
        "train_pf": train["pf"], "h1_ret": h1["ret"], "h2_ret": h2["ret"],
        "test_ret": test["ret"], "test_dd": test["dd"],
        "test_trades": test["trades"], "test_wins": test["wins"],
        "test_pf": test["pf"], "full_ret": full["ret"],
        "full_dd": full["dd"], "score": score,
    }


def main():
    out = HERE / "top5_scan.csv"
    rows = []
    done = set()
    if out.exists():
        old = pd.read_csv(out)
        rows = old.to_dict("records")
        done = {(r["symbol"], r["tf"], r["config"]) for r in rows}
        print(f"resume with {len(rows)} rows", flush=True)
    for tf in TFS:
        configs = grid(tf)
        print(f"== {tf}: {len(configs)} configs ==", flush=True)
        for sym in universe():
            try:
                bars = load_scan_bars(sym, tf, source="binance", days=LOAD_DAYS)
            except Exception as exc:
                print(f"skip {sym} {tf}: {exc}", flush=True)
                continue
            if len(bars) < MIN_TRAIN_BARS:
                print(f"short {sym} {tf}: {len(bars)}", flush=True)
                continue
            for label, strat in configs:
                if (sym, tf, label) in done:
                    continue
                try:
                    m = run(sym, tf, label, strat, bars)
                except Exception as exc:
                    print(f"!! {sym} {tf} {label}: {exc}", flush=True)
                    continue
                if m:
                    rows.append(m)
                    done.add((sym, tf, label))
            print(f"  {sym} done ({len(rows)} rows)", flush=True)
            pd.DataFrame(rows).to_csv(out, index=False)
    d = pd.DataFrame(rows)
    out = HERE / "top5_scan.csv"
    d.to_csv(out, index=False)
    print(f"wrote {out} rows={len(d)}")
    best = d.sort_values("score", ascending=False).groupby("symbol", as_index=False).first()
    cols = ["symbol", "tf", "config", "train_ret", "train_dd", "train_trades",
            "h1_ret", "h2_ret", "test_ret", "test_dd", "test_trades", "test_pf", "score"]
    show = best.sort_values("score", ascending=False).head(15)[cols].copy()
    for c in ("train_ret", "train_dd", "h1_ret", "h2_ret", "test_ret", "test_dd", "score"):
        show[c] = show[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
