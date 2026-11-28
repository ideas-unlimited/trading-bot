"""The literal WVFX reading vs the deployed one (research, 2 years, 4h).

User rules, taken word for word:
  1. WVF green bar        - wvf >= upper_band or wvf >= range_high, on THIS bar
  2. stochastic oversold  - %K < 20 at some point in the last 5 bars
  3. trigger              - %K >= %D and the WVF bar is green
and take every entry that satisfies all three.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "research")

import wvfx_portfolio as wp
import wvfx_reanalyze as wr
from bot.scanlib import load_scan_bars

COINS = wr.SYMS
CEILING = pd.Timestamp("2026-11-28", tz="UTC")


def load(sym: str) -> pd.DataFrame:
    bars = load_scan_bars(sym, "4h", source="binance", days=730)
    df = pd.DataFrame({
        "ts": pd.to_datetime([b.ts for b in bars], utc=True),
        "open": [b.open for b in bars], "high": [b.high for b in bars],
        "low": [b.low for b in bars], "close": [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })
    return df.sort_values("ts").reset_index(drop=True)


def feats() -> dict[str, pd.DataFrame]:
    return {s: wr.features(load(s)) for s in COINS}


def rule_literal(s, i, row, btc_bull, bear_only):
    if bear_only and btc_bull is not None:
        b = btc_bull.reindex([row["ts"]]).iloc[0]
        if not (b == 0):
            return False
    return bool(row["green"]) and bool(row["over_5"]) and bool(row["k"] >= row["d"])


def rule_first_cross(s, i, row, btc_bull, bear_only):
    if bear_only and btc_bull is not None:
        b = btc_bull.reindex([row["ts"]]).iloc[0]
        if not (b == 0):
            return False
    return bool(row["green_event"]) and bool(row["over_5"]) and bool(row["cross_up"])


def rule_first_state(s, i, row, btc_bull, bear_only):
    if bear_only and btc_bull is not None:
        b = btc_bull.reindex([row["ts"]]).iloc[0]
        if not (b == 0):
            return False
    return bool(row["green_event"]) and bool(row["over_5"]) and bool(row["k"] >= row["d"])


def rule_any5_state(s, i, row, btc_bull, bear_only):
    if bear_only and btc_bull is not None:
        b = btc_bull.reindex([row["ts"]]).iloc[0]
        if not (b == 0):
            return False
    return bool(row["green_5"]) and bool(row["over_5"]) and bool(row["k"] >= row["d"])


def split_stats(eq, tr, feats_map):
    out = []
    for label, lo, hi in (("yr1", None, "2025-11-28"), ("yr2", "2025-11-28", None)):
        e = eq.copy()
        if lo:
            e = e[e.index >= pd.Timestamp(lo, tz="UTC")]
        if hi:
            e = e[e.index < pd.Timestamp(hi, tz="UTC")]
        if len(e) < 10:
            out.append("n/a")
            continue
        r = e.pct_change().dropna()
        out.append("%+6.1f%% dd %5.1f%%" % ((e.iloc[-1] / e.iloc[0] - 1) * 100,
                                           float((e / e.cummax() - 1).min()) * 100))
    return out


def main():
    f = feats()
    rows = [("first green + cross (deployed)", rule_first_cross),
            ("first green + state (k>=d)", rule_first_state),
            ("LITERAL: green now + over5 + k>=d", rule_literal),
            ("old any-green-5 + over5 + k>=d", rule_any5_state)]
    print("%-38s %7s %6s %7s %6s %5s %7s  %-30s" % (
        "rule (no bear filter)", "ret", "dd", "sharpe", "win", "n", "expR", "yr1 | yr2"))
    for name, rule in rows:
        eq, tr, _ = wp.simulate(f, rule, stop_mult=2.0, rr=2.0, max_hold=40,
                                risk_frac=0.015, max_pos=3, max_pos_pct=0.34,
                                fee_bps=10, bear_only=False)
        st = wp.stats(eq, tr)
        a, b = split_stats(eq, tr, f)
        print("%-38s %+6.1f%% %5.1f%% %7.2f %5.0f%% %5d %+7.3f  %s | %s"
              % (name, st["ret"] * 100, st["dd"] * 100, st["sharpe"], st["win"] * 100,
                 st["n"], st["avgR"], a, b))


if __name__ == "__main__":
    main()