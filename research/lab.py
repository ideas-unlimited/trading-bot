
"""Research lab: features, triple-barrier labels, walk-forward calibration.

Everything here is past-only by construction: a feature at bar i uses bars
<= i, a label starts at bar i+1's open (the earliest a signal could fill).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "crypto"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT")
BARS_PER_YEAR = {"1d": 365.0, "4h": 365.0 * 6}


def load(sym: str, tf: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{sym}_{tf}.csv")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_localize(None)
    return df.drop(columns=["timestamp"]).sort_values("ts").reset_index(drop=True)


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn > 0, 100.0)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns used by both features and the live strategy."""
    d = df.copy()
    c, h, l, o, v = d["close"], d["high"], d["low"], d["open"], d["volume"]
    d["atr"] = _atr(d, 14)
    d["atr_pct"] = d["atr"] / c
    d["atr50"] = _atr(d, 50)
    d["ema20"] = _ema(c, 20)
    d["ema50"] = _ema(c, 50)
    d["ema200"] = _ema(c, 200)
    d["sma200"] = c.rolling(200, min_periods=200).mean()
    d["rsi14"] = _rsi(c, 14)
    d["sd20"] = c.rolling(20, min_periods=20).std(ddof=0)
    d["hi20"] = h.rolling(20, min_periods=20).max()
    d["lo20"] = l.rolling(20, min_periods=20).min()
    d["hi55"] = h.rolling(55, min_periods=55).max()
    d["lo55"] = l.rolling(55, min_periods=55).min()
    d["vol_sma20"] = v.rolling(20, min_periods=20).mean()
    d["ret1"] = np.log(c).diff()
    d["range"] = (h - l).replace(0, np.nan)
    d["body"] = (c - o) / d["range"]
    d["upper_wick"] = (h - np.maximum(c, o)) / d["range"]
    d["lower_wick"] = (np.minimum(c, o) - l) / d["range"]
    d["clv"] = (c - l) / d["range"]  # close location in bar range
    return d


FEATURES = [
    "r5", "r10", "r20", "r60", "r120",
    "px_ema20", "ema20_50", "px_ema50", "ema50_200", "px_sma200",
    "atr_pct", "atr_ratio", "rsi", "z20", "dist_hi20", "dist_lo20",
    "body", "clv", "vol_z", "vs_btc20", "cs20", "cs60",
    "btc_regime", "btc_dd", "btc_vol_pct", "regime_x_rsi", "mom_x_regime",
]


def features(df: pd.DataFrame, btc: pd.DataFrame, peers: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build the model matrix; every column is known at bar i's close."""
    d = df.copy()
    c = d["close"]
    d["r5"] = np.log(c / c.shift(5))
    d["r10"] = np.log(c / c.shift(10))
    d["r20"] = np.log(c / c.shift(20))
    d["r60"] = np.log(c / c.shift(60))
    d["r120"] = np.log(c / c.shift(120))
    d["px_ema20"] = c / d["ema20"] - 1
    d["ema20_50"] = d["ema20"] / d["ema50"] - 1
    d["px_ema50"] = c / d["ema50"] - 1
    d["ema50_200"] = d["ema50"] / d["ema200"] - 1
    d["px_sma200"] = c / d["sma200"] - 1
    d["atr_ratio"] = d["atr"] / d["atr50"]
    d["rsi"] = (d["rsi14"] - 50.0) / 50.0
    d["z20"] = (c - d["ema20"]) / d["sd20"].replace(0, np.nan)
    d["dist_hi20"] = c / d["hi20"] - 1
    d["dist_lo20"] = c / d["lo20"] - 1
    d["vol_z"] = (np.log(d["volume"] / d["vol_sma20"])).clip(-3, 3)

    # BTC context: the market beta regime every coin lives inside.
    b = btc[["ts", "close", "sma200", "atr_pct"]].copy()
    b["btc_regime"] = b["close"] / b["sma200"] - 1
    b["btc_dd"] = b["close"] / b["close"].rolling(180, min_periods=60).max() - 1
    b["btc_vol_pct"] = b["atr_pct"].rolling(365, min_periods=60).rank(pct=True)
    b["btc_r20"] = np.log(b["close"] / b["close"].shift(20))
    d = d.merge(b[["ts", "btc_regime", "btc_dd", "btc_vol_pct", "btc_r20"]], on="ts", how="left")

    # Cross-sectional rank of momentum across the universe.
    pe = []
    for s, pdf in peers.items():
        q = pdf[["ts", "close"]].copy()
        q[f"r20_{s}"] = np.log(q["close"] / q["close"].shift(20))
        q[f"r60_{s}"] = np.log(q["close"] / q["close"].shift(60))
        pe.append(q[["ts", f"r20_{s}", f"r60_{s}"]])
    x = pe[0]
    for q in pe[1:]:
        x = x.merge(q, on="ts", how="outer")
    r20 = x[[c for c in x if c.startswith("r20_")]]
    r60 = x[[c for c in x if c.startswith("r60_")]]
    x["cs20"] = (r20.rank(axis=1, pct=True).mean(axis=1) - 0.5) * 2
    x["cs60"] = (r60.rank(axis=1, pct=True).mean(axis=1) - 0.5) * 2
    # Per-symbol percentile, not the average across symbols.
    x = x[["ts", "cs20", "cs60"]]
    d = d.merge(x, on="ts", how="left")

    d["vs_btc20"] = d["r20"] - d["btc_r20"]
    d["regime_x_rsi"] = d["btc_regime"].clip(-0.5, 0.5) * d["rsi"]
    d["mom_x_regime"] = d["r20"].clip(-0.5, 0.5) * d["btc_regime"].clip(-0.5, 0.5)
    return d


def label(d: pd.DataFrame, *, stop_mult: float = 2.0, rr: float = 1.5,
          horizon: int = 40) -> pd.DataFrame:
    """Triple-barrier label for a long entered at the NEXT bar's open.

    Returns columns: y (1 = target first), r_mult (realised R), bars_held,
    entry, stop, target.  Ambiguity within a bar resolves to the stop.
    """
    o = d["open"].to_numpy(float)
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    a = d["atr"].to_numpy(float)
    n = len(d)
    y = np.full(n, np.nan)
    rm = np.full(n, np.nan)
    held = np.full(n, np.nan)
    entry = np.full(n, np.nan)
    stop = np.full(n, np.nan)
    tgt = np.full(n, np.nan)
    for i in range(n - 1):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        e = o[i + 1]
        s = e - stop_mult * a[i]
        t = e + rr * stop_mult * a[i]
        entry[i], stop[i], tgt[i] = e, s, t
        end = min(n, i + 1 + horizon)
        for j in range(i + 1, end):
            if l[j] <= s:
                y[i] = 0.0
                rm[i] = (min(o[j], s) - e) / (e - s)
                held[i] = j - i
                break
            if h[j] >= t:
                y[i] = 1.0
                rm[i] = (max(o[j], t) - e) / (e - s)
                held[i] = j - i
                break
        else:
            j = end - 1
            y[i] = 0.0  # timeout counts as not hitting the target
            rm[i] = (o[j] - e) / (e - s)
            held[i] = j - i
    out = pd.DataFrame({"y": y, "r_mult": rm, "bars_held": held,
                        "entry": entry, "stop": stop, "target": tgt}, index=d.index)
    return pd.concat([d, out], axis=1)


def build(tf: str, **label_kw) -> dict[str, pd.DataFrame]:
    raw = {s: load(s, tf) for s in SYMBOLS}
    enr = {s: enrich(raw[s]) for s in SYMBOLS}
    btc = enr["BTCUSDT"]
    out = {}
    for s in SYMBOLS:
        f = features(enr[s], btc, enr)
        out[s] = label(f, **label_kw)
    return out
