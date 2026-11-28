"""WVF with its own-coin bear filter, on a wider pool (research)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "research")

import wvfx_reanalyze as wr
import wvfx_trail as wt
from bot.scanlib import load_scan_bars

WIDE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
        "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT",
        "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT"]


def load(sym):
    bars = load_scan_bars(sym, "4h", source="binance", days=730)
    if len(bars) < 1500:
        return None
    df = pd.DataFrame({
        "ts": pd.to_datetime([b.ts for b in bars], utc=True),
        "open": [b.open for b in bars], "high": [b.high for b in bars],
        "low": [b.low for b in bars], "close": [b.close for b in bars],
        "volume": [b.volume for b in bars],
    }).sort_values("ts").reset_index(drop=True)
    return wr.features(df)


def build(syms):
    out = {}
    for s in syms:
        df = load(s)
        if df is None:
            continue
        sma = df["close"].rolling(1200, min_periods=1200).mean()
        df["bear"] = (df["close"] < sma).fillna(False).astype(bool)
        out[s] = df
    return out


def run(f, **kw):
    series, rs = wt.simulate(f, bear=True, **kw)
    r = series.pct_change().dropna()
    return (series.iloc[-1] / 1000 - 1) * 100, float((series / series.cummax() - 1).min()) * 100, \
        r.mean() / r.std() * np.sqrt(2190), len(rs), rs.mean() * 100


def main():
    five = build(wr.SYMS)
    wide = build(WIDE)
    print("five coins:", len(five), " wide pool:", len(wide))
    print("%-40s %7s %7s %6s %5s %6s" % ("pool / risk / slots", "ret", "dd", "sharpe", "n", "avgR"))
    for label, f in (("five", five), ("wide", wide)):
        for risk, slots in ((0.015, 3), (0.01, 3), (0.02, 3), (0.015, 5)):
            ret, dd, sh, n, avg = run(f, risk_frac=risk, max_pos=slots)
            print("%-40s %+6.1f%% %+6.1f%% %6.2f %5d %+6.3f"
                  % ("%s risk %.1f%% slots %d" % (label, risk * 100, slots), ret, dd, sh, n, avg))


if __name__ == "__main__":
    main()