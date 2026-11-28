"""Stage 3: the evidence pack for broad-sweep finalists.

Same numbers the shipped sleeves carry (Sharpe/Sortino/Calmar, PF, expectancy
in R, Wilson win-rate interval, 5,000-draw bootstrap, H1/H2, 2-year
cross-check), computed with the same helpers as `five_sleeves.py` so a winner
here is directly comparable with `capitulation_dip_uni` or
`swing_breakout_arb`.

    python research/broad_evidence.py                     # auto from stage 2
    python research/broad_evidence.py --picks "ZEC/USDT|lab breakout 120h s3 t5"
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_deep import cell_map  # noqa: E402
from allcoins_sweep import BAR_HOURS, LOAD_DAYS, run_one  # noqa: E402
from five_sleeves import analyse, bootstrap  # noqa: E402
from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

WINDOW_DAYS = 365
MIN_TRADES = 8
MIN_DAYS = 330


def cfg(bpy: float) -> BotConfig:
    return BotConfig(initial_cash=1000.0, costs=CostConfig(),
                     risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95,
                                     max_drawdown_stop=0.99),
                     bars_per_year=bpy)


def run(df: pd.DataFrame, strat: object, tf: str, start_ts) -> dict | None:
    if hasattr(strat, "reset"):
        strat.reset()
    eng = Engine(df, strat, cfg(365.0 * 24.0 / BAR_HOURS[tf]))
    eng.run()
    return analyse(eng.result(), start_ts)


def main() -> None:
    specs: list[tuple[str, str, str]] = []
    if "--picks" in sys.argv:
        for spec in sys.argv[sys.argv.index("--picks") + 1].split(","):
            pair, label = spec.split("|", 1)
            specs.append((pair, label, ""))
    else:
        d = pd.read_csv(HERE / "broad_stage2.csv")
        ok = d[(d.tfs_positive >= 3) & (d.neigh_median > 0) & (d.base_trades >= MIN_TRADES)]
        ok = ok.sort_values(["base_calmar", "base_ret"], ascending=False).head(10)
        specs = [(r.pair, r.config, r.base_tf) for r in ok.itertuples()]
    print(f"{len(specs)} finalists")
    rows = []
    for pair, label, base_tf in specs:
        tfs = [base_tf] if base_tf else ["4h", "1d"]
        for tf in tfs:
            strat = cell_map(tf).get(label)
            if strat is None:
                continue
            bars = load_scan_bars(pair, tf, source="binance", days=LOAD_DAYS)
            if len(bars) < 200:
                continue
            df = bars_to_frame(bars)
            start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
            win = df[df["ts"] >= start_ts]
            days = float((win["ts"].iloc[-1] - win["ts"].iloc[0]).days)
            if days < MIN_DAYS:
                print(f"  skip {pair} {tf}: {days:.0f} days of history")
                continue
            m = run(df, strat, tf, start_ts)
            if m is None or m["trades"] < MIN_TRADES:
                continue
            boot = bootstrap(m.pop("rs"))
            # 2-year cross-check on a fresh instance of the same rule
            long = load_scan_bars(pair, tf, source="binance", days=730)
            ldf = bars_to_frame(long)
            lstart = ldf["ts"].iloc[-1] - pd.Timedelta(days=730)
            ldays = float((ldf[ldf["ts"] >= lstart]["ts"].iloc[-1]
                           - ldf[ldf["ts"] >= lstart]["ts"].iloc[0]).days)
            m2 = run(ldf, strat, tf, lstart) if ldays > 400 else None
            row = dict(pair=pair, config=label, tf=tf, window_days=days, **m)
            row.update({f"boot_{k}": v for k, v in boot.items()})
            row["two_year_ret"] = (m2["ret"] if m2 else float("nan"))
            row["two_year_dd"] = (m2["dd"] if m2 else float("nan"))
            row["two_year_trades"] = (m2["trades"] if m2 else 0)
            rows.append(row)
            print(f"  {pair:>12} {tf:3} {label:<26} {m['ret']:+7.1%} "
                  f"dd {m['dd']:+6.1%} tr {m['trades']:3} "
                  f"p_pos {boot['p_pos']:.2f} 2y {row['two_year_ret']:+7.1%}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "broad_evidence.csv", index=False)
    cols = ["pair", "tf", "config", "ret", "dd", "calmar", "sharpe", "sortino",
            "pf", "exp_r", "trades", "win", "win_lo", "win_hi", "h1_ret", "h2_ret",
            "boot_p_pos", "boot_p05", "boot_p95", "two_year_ret", "two_year_dd"]
    tbl = out.sort_values("boot_p_pos", ascending=False)[cols].copy()
    for c in ("ret", "dd", "h1_ret", "h2_ret", "boot_p05", "boot_p95",
              "two_year_ret", "two_year_dd"):
        tbl[c] = tbl[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    for c in ("calmar", "sharpe", "sortino", "pf", "exp_r", "boot_p_pos"):
        tbl[c] = tbl[c].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
    tbl["win"] = [f"{w:.0%} ({lo:.0%}-{hi:.0%})" if w == w else ""
                  for w, lo, hi in zip(tbl["win"], tbl["win_lo"], tbl["win_hi"])]
    tbl = tbl.drop(columns=["win_lo", "win_hi"])
    print(tbl.to_string(index=False))


if __name__ == "__main__":
    main()