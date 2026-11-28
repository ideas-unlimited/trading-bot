"""Promotion evidence for the four shipped sleeves (research, not a study).

Re-runs each deployable sleeve through the engine on the same 455-day load the
five-sleeve study used, slices the reported 365-day window, and writes the
evidence JSON that `bot promote` needs.  The point is that the number on the
manifest is produced by the shipped class, not copied from a spreadsheet.

    python research/five_sleeves_promote.py            # print the table
    python research/five_sleeves_promote.py --write    # + research/evidence/*.json

Window: last 365 days of a 455-day load (warm-up burns outside), 4h, Binance
spot, next-open fills, 10 bps fee + 5 bps slippage a side, 3% risk, long only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.promotions import params_hash  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402
from five_sleeves import LOAD_DAYS, RISK, WINDOW_DAYS, analyse, cfg  # noqa: E402

SLEEVES = [
    ("NEAR/USDT", "swing_pullback_near"),
    ("RENDER/USDT", "swing_breakout_render"),
    ("ARB/USDT", "swing_breakout_arb"),
    ("JUP/USDT", "capitulation_dip_jup"),
]
OUT = Path("research/evidence")


def evidence(symbol: str, name: str) -> dict:
    bars = load_scan_bars(symbol, "4h", source="binance", days=LOAD_DAYS)
    df = bars_to_frame(bars)
    start = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    strat = build(name)
    eng = Engine(df, strat, cfg())
    eng.run()
    m = analyse(eng.result(), start)
    window = df[df["ts"] >= start]
    return {
        "symbol": symbol,
        "timeframe": "4h",
        "window": f"{start.date()} .. {df['ts'].iloc[-1].date()}",
        "bars": int(len(window)),
        "window_days": int(WINDOW_DAYS),
        "trades": int(m["trades"]),
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
        "return_pct": round(float(m["ret"]), 4),
        "max_drawdown": round(float(m["dd"]), 4),
        "win_rate": round(float(m["win"]), 4),
        "profit_factor": round(float(m["pf"]), 2),
        "params_hash": params_hash(strat.params),
        "command": (
            f"python research/five_sleeves_promote.py  # {name} on {symbol}, "
            f"4h, {start.date()} .. {df['ts'].iloc[-1].date()}"
        ),
        "note": (
            "shipped sleeve re-run through Engine: next-open fills, stop-first "
            f"intrabar, {RISK:.0%} risk, 10 bps fee + 5 bps slippage a side; "
            "window = last 365 days of a 455-day load"
        ),
    }


def main(argv: list[str]) -> int:
    write = "--write" in argv
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
    print(f"{'symbol':13s} {'strategy':24s} {'trades':>6s} {'ret':>8s} {'dd':>8s} {'win':>7s} {'pf':>6s}")
    for symbol, name in SLEEVES:
        ev = evidence(symbol, name)
        print(f"{symbol:13s} {name:24s} {ev['trades']:6d} {ev['return_pct']*100:+7.1f}% "
              f"{ev['max_drawdown']*100:7.1f}% {ev['win_rate']*100:6.1f}% {ev['profit_factor']:6.2f}")
        if write:
            path = OUT / f"{name}.json"
            path.write_text(json.dumps(ev, indent=2) + chr(10), encoding="utf-8")
            print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
