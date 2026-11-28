"""Lever search on the 3-year window using the ported PM indicators.

Scratch research: reproduces the bot.book loop (one wallet, N slots, fills
at next open, stop intrabar, trail ratcheted after the exit check) so new
indicators can be tried as entry / exit / regime levers before promotion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot import analysis as an
from bot.book import bars_to_frame, load_bars_from_csv

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
START = pd.Timestamp("2023-11-26", tz="UTC")
ONE_YEAR = pd.Timestamp("2025-11-26", tz="UTC")
BPY = {"15m": 96 * 365.0, "1h": 24 * 365.0, "4h": 6 * 365.0, "1d": 365.0}
LADDER = {
    "15m": [(120, 200), (240, 400), (480, 800), (960, 1600)],
    "1h": [(30, 50), (60, 100), (120, 200), (240, 400)],
    "4h": [(15, 25), (30, 50), (60, 100), (120, 200)],
    "1d": [(5, 8), (10, 18), (20, 33), (40, 66)],
}
_TF = "4h"


def an_atr(f, n=14):
    from bot.indicators import atr
    return atr(f["high"], f["low"], f["close"], n)


def build(tf: str | None = None):
    global _TF
    if tf is not None:
        _TF = tf
    bars = load_bars_from_csv("data/crypto", COINS, _TF)
    frames = {s: bars_to_frame(b) for s, b in bars.items()}
    tl = None
    for f in frames.values():
        tl = f.index if tl is None else tl.union(f.index)
    data = {}
    for s, f in frames.items():
        close, high, low = f["close"], f["high"], f["low"]
        sma_f = close.rolling(30, min_periods=30).mean()
        sma_s = close.rolling(50, min_periods=50).mean()
        st = an.supertrend(f, 10, 3.0)
        rf = an.range_filter(f, 100, 3.0)
        ci = an.choppiness_index(f, 14)
        dv = an.damiani_volatmeter(f)
        stc = an.schaff_trend_cycle(close)
        er = an.efficiency_ratio(close, 20)
        adx_v = an.adx(high, low, close)[0]
        ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()

        def re(x):
            return x.reindex(tl)

        data[s] = {
            "o": re(f["open"]).to_numpy(float),
            "h": re(high).to_numpy(float),
            "l": re(low).to_numpy(float),
            "c": re(close).to_numpy(float),
            "atr": re(an_atr(f)).to_numpy(float),
            "cross_up": ((sma_f > sma_s) & (sma_f.shift(1) <= sma_s.shift(1)))
                         .reindex(tl).fillna(False).to_numpy(bool),
            "cross_dn": ((sma_f < sma_s) & (sma_f.shift(1) >= sma_s.shift(1)))
                         .reindex(tl).fillna(False).to_numpy(bool),
            "st_trend": re(st["trend"]).to_numpy(float),
            "st_line": re(st["line"]).to_numpy(float),
            "st_up": (st["flip"] & (st["trend"] == 1)).reindex(tl).fillna(False).to_numpy(bool),
            "st_dn": (st["flip"] & (st["trend"] == -1)).reindex(tl).fillna(False).to_numpy(bool),
            "rf_state": re(rf["state"]).to_numpy(float),
            "ci": re(ci).to_numpy(float),
            "dv": re(dv["expanding"]).fillna(False).to_numpy(bool),
            "stc": re(stc).to_numpy(float),
            "er": re(er).to_numpy(float),
            "adx": re(adx_v).to_numpy(float),
            "ema200": re(ema200).to_numpy(float),
        }
    return tl, data


def mask_not(x):
    return np.nan_to_num(x, nan=0.0).astype(bool)


def sim(tl, data, entry, exit_, stop_atr=2.0, trail=None, risk=0.02, cap=0.95,
        fee=0.001, slip=0.0005, cash0=1000.0, stop_price=None, start=None,
        max_pos=1):
    ts = pd.Timestamp(start)
    if tl.tz is not None and ts.tz is None:
        ts = ts.tz_localize(tl.tz)
    i0 = int(tl.searchsorted(ts)) if start is not None else 0
    n = len(tl)
    cash = cash0
    pos = {}
    pending_buy = []
    pending_sell = []
    eq = np.full(n, np.nan)
    trades = []
    bars_in = 0

    def mark(i):
        v = cash
        for s, p in pos.items():
            c = data[s]["c"][i]
            v += p["qty"] * (c if np.isfinite(c) else p["entry"])
        return v

    def close_pos(s, i, px, why):
        nonlocal cash
        p = pos.pop(s)
        fill = px * (1.0 - slip)
        cash += p["qty"] * fill * (1.0 - fee)
        r = (fill - p["entry"]) / p["risk0"] if p["risk0"] > 0 else 0.0
        fees = p["qty"] * (p["entry"] + fill) * fee
        trades.append({"sym": s, "i": i, "r": r, "why": why,
                       "ret": fill / p["entry"] - 1.0,
                       "pnl": p["qty"] * (fill - p["entry"]) - fees,
                       "fee": fees})

    for i in range(i0, n):
        for s in pending_sell:
            if s in pos:
                px = data[s]["o"][i]
                if np.isfinite(px):
                    close_pos(s, i, px, "signal")
        pending_sell = []

        for _score, s, stop0 in pending_buy:
            if len(pos) >= max_pos or s in pos:
                continue
            px = data[s]["o"][i] * (1.0 + slip)
            if not np.isfinite(px) or not 0 < stop0 < px:
                continue
            equity = mark(i)
            q = min(risk * equity / (px - stop0), cap * equity / px,
                    cash / (px * (1.0 + fee)))
            if q * px < 1.0:
                continue
            cash -= q * px * (1.0 + fee)
            pos[s] = {"qty": q, "entry": px, "stop": stop0, "risk0": px - stop0}
        pending_buy = []

        for s in list(pos):
            lo = data[s]["l"][i]
            if np.isfinite(lo) and lo <= pos[s]["stop"]:
                close_pos(s, i, min(data[s]["o"][i], pos[s]["stop"]), "stop")

        if trail is not None:
            for s in list(pos):
                cand = trail[s][i]
                if np.isfinite(cand) and cand > pos[s]["stop"]:
                    pos[s]["stop"] = cand

        eq[i] = mark(i)
        if pos:
            bars_in += 1

        cands = []
        for s in data:
            c = data[s]["c"][i]
            if not np.isfinite(c):
                continue
            if s in pos and exit_[s][i]:
                pending_sell.append(s)
            elif s not in pos and entry[s][i]:
                st = stop_price[s][i] if stop_price is not None else np.nan
                if not np.isfinite(st):
                    a = data[s]["atr"][i]
                    st = c - stop_atr * a if np.isfinite(a) else np.nan
                if not np.isfinite(st) or st <= 0:
                    continue
                k = i - 30
                score = c / data[s]["c"][k] - 1.0 if k >= 0 else -9.0
                cands.append((score, s, float(st)))
        free = max_pos - len(pos) + len(pending_sell)
        if cands and free > 0:
            cands.sort(reverse=True)
            for sc, s, st in cands[:free]:
                if s not in pending_sell:
                    pending_buy.append((sc, s, st))

    for s in list(pos):
        close_pos(s, n - 1, data[s]["c"][n - 1], "end")
    eq[n - 1] = cash

    eqs = pd.Series(eq, index=tl).dropna()
    return eqs, trades, bars_in / max(len(eqs), 1)


def metrics(eq, trades, exposure, cash0=1000.0):
    bpy = BPY[_TF]
    rets = eq.pct_change().dropna()
    years = len(rets) / bpy
    total = eq.iloc[-1] / cash0 - 1.0
    cagr = (eq.iloc[-1] / cash0) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    vol = float(rets.std() * np.sqrt(bpy))
    sharpe = float(rets.mean() * bpy / vol) if vol > 0 else np.nan
    dd = float((eq / eq.cummax() - 1.0).min())
    rs = np.array([t["r"] for t in trades])
    wins = rs[rs > 0]
    return {"total": total, "cagr": cagr, "sharpe": sharpe, "dd": dd,
            "trades": len(rs), "win": len(wins) / len(rs) if len(rs) else np.nan,
            "avg_r": rs.mean() if len(rs) else np.nan, "exposure": exposure,
            "calmar": cagr / abs(dd) if dd < 0 else np.nan}


def buckets(eq):
    tz = eq.index.tz
    pts = [eq.index[0], pd.Timestamp("2024-11-26", tz=tz),
           pd.Timestamp("2025-11-26", tz=tz), eq.index[-1]]
    out = []
    for a, b in zip(pts, pts[1:]):
        seg = eq.loc[a:b]
        out.append(seg.iloc[-1] / seg.iloc[0] - 1.0 if len(seg) > 1 else np.nan)
    return out


def line(label, m, bk=None):
    b = "" if bk is None else "   [" + " ".join("%+6.1f" % (x * 100) for x in bk) + "]"
    print("%-46s %7.0f%%  dd %6.1f%%  sh %4.2f  calm %5.2f  n %3d  R %+5.2f%s" % (
        label, m["total"] * 100, m["dd"] * 100, m["sharpe"], m["calmar"],
        m["trades"], m["avg_r"], b))


def main():
    tl, data = build()
    n = len(tl)
    zero = {s: np.zeros(n, dtype=bool) for s in data}
    base_entry = {s: data[s]["cross_up"] for s in data}
    base_exit = {s: data[s]["cross_dn"] for s in data}

    def run(entry, exit_, **kw):
        eq, tr, ex = sim(tl, data, entry, exit_, start=START, **kw)
        return metrics(eq, tr, ex), eq

    m, eq = run(base_entry, base_exit)
    line("BASE 1 slot, risk 2%, 2atr stop", m, buckets(eq))

    print()
    print("--- capacity: slots x risk (base rule, no filter) ---")
    for mp in (1, 2, 3):
        for rk in (0.02, 0.03, 0.04):
            m, eq = run(base_entry, base_exit, max_pos=mp, risk=rk)
            line("slots %d risk %.0f%%" % (mp, rk * 100), m, buckets(eq))

    print()
    print("--- regime filters (1 slot, risk 2%) ---")
    filters = {
        "chop<50": {s: data[s]["ci"] < 50 for s in data},
        "chop<45": {s: data[s]["ci"] < 45 for s in data},
        "damiani green": {s: data[s]["dv"] for s in data},
        "supertrend up": {s: data[s]["st_trend"] == 1 for s in data},
        "stc>50": {s: data[s]["stc"] > 50 for s in data},
        "ema200": {s: data[s]["c"] > data[s]["ema200"] for s in data},
        "adx>25": {s: data[s]["adx"] > 25 for s in data},
    }
    for name, mask in filters.items():
        e = {s: data[s]["cross_up"] & mask_not(mask[s]) for s in data}
        m, eq = run(e, base_exit)
        line("filter " + name, m, buckets(eq))

    print()
    print("--- combos of the best filters (1 slot, risk 2%) ---")
    combos = {
        "damiani + stc>50": ["damiani green", "stc>50"],
        "damiani + chop<50": ["damiani green", "chop<50"],
        "damiani + supertrend up": ["damiani green", "supertrend up"],
        "damiani + ema200": ["damiani green", "ema200"],
        "chop<50 + stc>50": ["chop<50", "stc>50"],
        "chop<45 + supertrend up": ["chop<45", "supertrend up"],
        "stc>50 + ema200": ["stc>50", "ema200"],
    }
    for name, keys in combos.items():
        e = {}
        for s in data:
            ok = np.ones(n, dtype=bool)
            for k in keys:
                ok &= mask_not(filters[k][s])
            e[s] = data[s]["cross_up"] & ok
        m, eq = run(e, base_exit)
        line(name, m, buckets(eq))

    print()
    print("--- best combos x slots/risk ---")
    best = {"damiani green": ["damiani green"], "damiani + stc>50": ["damiani green", "stc>50"],
            "damiani + chop<50": ["damiani green", "chop<50"]}
    for name, keys in best.items():
        e = {}
        for s in data:
            ok = np.ones(n, dtype=bool)
            for k in keys:
                ok &= mask_not(filters[k][s])
            e[s] = data[s]["cross_up"] & ok
        for mp, rk in ((1, 0.03), (2, 0.02), (2, 0.025), (2, 0.03)):
            m, eq = run(e, base_exit, max_pos=mp, risk=rk)
            line("%s | slots %d risk %.1f%%" % (name, mp, rk * 100), m, buckets(eq))



def cross_of(arr, tl, fast, slow, kind="sma"):
    s = pd.Series(arr, index=tl).astype(float)
    if kind == "ema":
        f = s.ewm(span=fast, adjust=False, min_periods=fast).mean()
        sl = s.ewm(span=slow, adjust=False, min_periods=slow).mean()
    else:
        f = s.rolling(fast, min_periods=fast).mean()
        sl = s.rolling(slow, min_periods=slow).mean()
    up = ((f > sl) & (f.shift(1) <= sl.shift(1))).fillna(False).to_numpy(bool)
    dn = ((f < sl) & (f.shift(1) >= sl.shift(1))).fillna(False).to_numpy(bool)
    return up, dn


def masks(tl, data, keys, **extra):
    n = len(tl)
    out = {}
    for s in data:
        ok = np.ones(n, dtype=bool)
        for k in keys:
            ok &= mask_not(extra[k][s])
        out[s] = ok
    return out


def robust():
    tl, data = build()
    n = len(tl)
    base_entry = {s: data[s]["cross_up"] for s in data}
    base_exit = {s: data[s]["cross_dn"] for s in data}

    def run(entry, exit_, **kw):
        eq, tr, ex = sim(tl, data, entry, exit_, start=START, **kw)
        return metrics(eq, tr, ex), eq, tr

    def filt(keys, **ov):
        f = {
            "chop": {s: data[s]["ci"] < ov.get("chop", 50) for s in data},
            "damiani": {s: data[s]["dv"] for s in data},
            "stc": {s: data[s]["stc"] > ov.get("stc", 50) for s in data},
            "st": {s: data[s]["st_trend"] == 1 for s in data},
            "ema": {s: data[s]["c"] > data[s]["ema200"] for s in data},
            "adx": {s: data[s]["adx"] > ov.get("adx", 25) for s in data},
        }
        return masks(tl, data, keys, **f)

    print("--- stop distance (slots 1) ---")
    for k in (1.5, 2.0, 2.5, 3.0):
        m, eq, _ = run(base_entry, base_exit, stop_atr=k)
        line("base stop %.1f atr" % k, m, buckets(eq))
    band = filt(["damiani", "stc"])
    for k in (1.5, 2.0, 2.5, 3.0):
        e = {s: base_entry[s] & band[s] for s in data}
        m, eq, _ = run(e, base_exit, stop_atr=k, risk=0.03)
        line("damiani+stc>50 stop %.1f atr, risk 3%%" % k, m, buckets(eq))

    print()
    print("--- STC threshold (with damiani, stop 2.0, risk 3%) ---")
    for th in (40, 45, 50, 55, 60, 70, 80):
        e = {s: base_entry[s] & filt(["damiani", "stc"], stc=th)[s] for s in data}
        m, eq, _ = run(e, base_exit, risk=0.03)
        line("stc>%d" % th, m, buckets(eq))

    print()
    print("--- chop threshold (with stc>50, stop 2.0, risk 3%) ---")
    for th in (40, 45, 50, 55, 61.8, 70):
        e = {s: base_entry[s] & filt(["chop", "stc"], chop=th)[s] for s in data}
        m, eq, _ = run(e, base_exit, risk=0.03)
        line("chop<%s" % th, m, buckets(eq))

    print()
    print("--- MA pair (damiani+stc, stop 2.0, risk 3%) ---")
    for kind, fa, sl in (("sma", 20, 50), ("sma", 30, 50), ("sma", 40, 60),
                         ("sma", 50, 100), ("ema", 20, 50), ("ema", 30, 50),
                         ("ema", 50, 100)):
        e, x = {}, {}
        for s in data:
            up, dn = cross_of(data[s]["c"], tl, fa, sl, kind)
            ok = filt(["damiani", "stc"])[s]
            e[s] = up & ok
            x[s] = dn
        m, eq, _ = run(e, x, risk=0.03)
        line("%s %d/%d" % (kind.upper(), fa, sl), m, buckets(eq))

    print()
    print("--- concentration of the leading config ---")
    for name, keys, rk in (("base 1/2%", [], 0.02), ("damiani+stc 1/3%", ["damiani", "stc"], 0.03)):
        e = {s: base_entry[s] & (masks(tl, data, keys, **{
            "damiani": {s: data[s]["dv"] for s in data},
            "stc": {s: data[s]["stc"] > 50 for s in data}})[s] if keys else base_entry[s])
            for s in data}
        m, eq, tr = run(e, base_exit, risk=rk)
        df = pd.DataFrame(tr)
        pnl = df.groupby("sym")["pnl"].sum().sort_values(ascending=False)
        tot = df["pnl"].sum()
        top = df["pnl"].max()
        print("%-22s total $%8.0f  top trade %5.1f%%  best coin %-8s %5.1f%%" % (
            name, tot, 100 * top / tot if tot else 0, pnl.index[0],
            100 * pnl.iloc[0] / tot if tot else 0))
        print("   by coin: " + "  ".join("%s %+.0f" % (k, v) for k, v in pnl.items()))



def recent():
    tl, data = build()
    n = len(tl)
    base_entry = {s: data[s]["cross_up"] for s in data}
    base_exit = {s: data[s]["cross_dn"] for s in data}

    def run(entry, **kw):
        eq, tr, ex = sim(tl, data, entry, base_exit, **kw)
        return metrics(eq, tr, ex)

    both = {s: base_entry[s] & data[s]["dv"] & (data[s]["stc"] > 50) for s in data}
    dam = {s: base_entry[s] & data[s]["dv"] for s in data}
    stc = {s: base_entry[s] & (data[s]["stc"] > 50) for s in data}
    starts = (("3y", START), ("1y", pd.Timestamp("2025-11-26", tz="UTC")))
    variants = (("base 2.0atr", base_entry, 2.0), ("base 1.5atr", base_entry, 1.5),
                ("damiani", dam, 2.0), ("stc>50", stc, 2.0),
                ("damiani+stc", both, 2.0), ("damiani+stc 1.5atr", both, 1.5))
    for tag, start in starts:
        print("--- %s (start %s) ---" % (tag, start.date()))
        for name, e, sa in variants:
            m = run(e, start=start, stop_atr=sa, risk=0.03)
            print("%-22s %7.0f%%  dd %6.1f%%  sh %4.2f  n %3d  R %+5.2f" % (
                name, m["total"] * 100, m["dd"] * 100, m["sharpe"],
                m["trades"], m["avg_r"]))
        print()


def placebo(draws=120, seed=11):
    tl, data = build()
    n = len(tl)
    base_entry = {s: data[s]["cross_up"] for s in data}
    base_exit = {s: data[s]["cross_dn"] for s in data}
    both = {s: base_entry[s] & data[s]["dv"] & (data[s]["stc"] > 50) for s in data}
    keep_idx = {}
    raw_idx = {}
    for s in data:
        raw_idx[s] = np.flatnonzero(base_entry[s])
        keep_idx[s] = np.flatnonzero(both[s])
    rng = np.random.default_rng(seed)
    eq, tr, ex = sim(tl, data, both, base_exit, start=START, stop_atr=1.5, risk=0.03)
    real = metrics(eq, tr, ex)
    totals = []
    shs = []
    for _ in range(draws):
        place = {}
        for s in data:
            k = len(keep_idx[s])
            pick = rng.choice(raw_idx[s], size=min(k, len(raw_idx[s])), replace=False)
            arr = np.zeros(n, dtype=bool)
            arr[pick] = True
            place[s] = arr
        eqp, trp, exp_ = sim(tl, data, place, base_exit, start=START, stop_atr=1.5, risk=0.03)
        m = metrics(eqp, trp, exp_)
        totals.append(m["total"])
        shs.append(m["sharpe"])
    totals = np.array(totals)
    shs = np.array(shs)
    pct = 100.0 * (totals < real["total"]).mean()
    pct_s = 100.0 * (shs < real["sharpe"]).mean()
    print("real  total %7.0f%%  sharpe %.2f" % (real["total"] * 100, real["sharpe"]))
    print("placebo (same trade count, random entries):")
    print("  total  median %7.0f%%  p5 %7.0f%%  p95 %7.0f%%" % (
        np.median(totals) * 100, np.percentile(totals, 5) * 100,
        np.percentile(totals, 95) * 100))
    print("  sharpe median %5.2f  p5 %5.2f  p95 %5.2f" % (
        np.median(shs), np.percentile(shs, 5), np.percentile(shs, 95)))
    print("  real beats %.0f%% of placebos on total, %.0f%% on sharpe" % (pct, pct_s))



def trail():
    tl, data = build()
    n = len(tl)
    base_entry = {s: data[s]["cross_up"] for s in data}
    base_exit = {s: data[s]["cross_dn"] for s in data}
    both = {s: base_entry[s] & data[s]["dv"] & (data[s]["stc"] > 50) for s in data}

    def run(entry, **kw):
        eq, tr, ex = sim(tl, data, entry, base_exit, start=START, **kw)
        return metrics(eq, tr, ex), eq

    print("--- stop + cross exit, optional ratchet (slots 1, risk 3%) ---")
    m, eq = run(base_entry, stop_atr=2.0, risk=0.03)
    line("plain 2.0atr stop, no trail", m, buckets(eq))
    for k in (3.0, 4.0, 5.0, 6.0):
        tr = {s: data[s]["c"] - k * data[s]["atr"] for s in data}
        m, eq = run(base_entry, stop_atr=2.0, risk=0.03, trail=tr)
        line("trail %.1f atr (ratchet) + cross exit" % k, m, buckets(eq))
    tr = {s: data[s]["st_line"] for s in data}
    m, eq = run(base_entry, stop_atr=2.0, risk=0.03, trail=tr)
    line("supertrend line ratchet + cross exit", m, buckets(eq))
    for k in (4.0, 5.0):
        tr = {s: data[s]["c"] - k * data[s]["atr"] for s in data}
        m, eq = run(both, stop_atr=2.0, risk=0.03, trail=tr)
        line("damiani+stc + trail %.1f atr" % k, m, buckets(eq))
    tr = {s: data[s]["st_line"] for s in data}
    m, eq = run(both, stop_atr=2.0, risk=0.03, trail=tr)
    line("damiani+stc + supertrend ratchet", m, buckets(eq))



def sweep(tfs=("15m", "1h", "4h", "1d"), atr_mult=2.0, risk=0.02):
    """Same wall-clock horizon, four timeframes: does a faster chart pay?"""
    for tf in tfs:
        try:
            tl, data = build(tf)
        except Exception as exc:
            print("==== %s: no data (%s)" % (tf, exc))
            continue
        start = START if tf in ("4h", "1d") else ONE_YEAR
        print("==== %s  (%d bars, test window from %s) ====" % (
            tf, len(tl), start.date()))
        for fast, slow in LADDER[tf]:
            up, dn = {}, {}
            gated = {}
            for s in data:
                u, d = cross_of(data[s]["c"], tl, fast, slow)
                up[s], dn[s] = u, d
                gated[s] = u & data[s]["dv"] & (data[s]["stc"] > 50)
            for tag, entry in (("plain", up), ("gated", gated)):
                eq, tr, ex = sim(tl, data, entry, dn, start=start,
                                 stop_atr=atr_mult, risk=risk)
                m = metrics(eq, tr, ex)
                print("%-4s %4d/%-4d %-5s $%7.0f  %7.0f%%  dd %6.1f%%  sh %4.2f  n %4d" % (
                    tf, fast, slow, tag, 1000 * (1 + m["total"]), m["total"] * 100,
                    m["dd"] * 100, m["sharpe"], m["trades"]))
        print()



def alt_entries():
    """Long-only entry ideas that are not a moving-average cross."""
    tl, data = build("4h")
    n = len(tl)
    base_exit = {s: data[s]["cross_dn"] for s in data}
    gate = {s: data[s]["dv"] & (data[s]["stc"] > 50) for s in data}

    def rmax(arr, k):
        return pd.Series(arr, index=tl).rolling(k).max().shift(1).to_numpy(float)

    def rmin(arr, k):
        return pd.Series(arr, index=tl).rolling(k).min().shift(1).to_numpy(float)

    plans = {}
    for s in data:
        c, h, l = data[s]["c"], data[s]["h"], data[s]["l"]
        stl, trend = data[s]["st_line"], data[s]["st_trend"]
        pull = (l <= stl) & (c > stl) & (trend == 1) & np.isfinite(stl)
        plans.setdefault("st pullback", {})[s] = pull & gate[s]
        plans.setdefault("st pullback vwap", {})[s] = pull & gate[s] & (c > data[s]["ema200"])
        breakout = c > rmax(h, 55)
        plans.setdefault("donchian 55 breakout", {})[s] = breakout
        plans.setdefault("donchian 55 + gate", {})[s] = breakout & gate[s]
        plans.setdefault("st flip + gate", {})[s] = data[s]["st_up"] & gate[s]

    def run(entry, exit_, **kw):
        eq, tr, ex = sim(tl, data, entry, exit_, start=START, **kw)
        return metrics(eq, tr, ex), eq

    print("--- alternative long-only entries (1 slot, 2%% risk, exit = 30/50 cross down) ---")
    for name, e in plans.items():
        m, eq = run(e, base_exit, stop_atr=2.0)
        line(name, m, buckets(eq))
    print()
    print("--- with their own exits where sensible ---")
    st_dn = {s: data[s]["st_dn"] for s in data}
    for name in ("st pullback", "st pullback vwap", "st flip + gate"):
        m, eq = run(plans[name], st_dn, stop_atr=2.0)
        line(name + " / exit st flip", m, buckets(eq))
    dc = {}
    dc_exit = {}
    for s in data:
        dc[s] = data[s]["c"] > rmax(data[s]["h"], 55)
        dc_exit[s] = data[s]["c"] < rmin(data[s]["l"], 20)
    m, eq = run(dc, dc_exit, stop_atr=2.0)
    line("donchian 55 / exit 20-low", m, buckets(eq))



def risk_grid():
    """How much of the account to put behind each idea: risk % x position cap."""
    tl, data = build("4h")
    gated = {s: data[s]["cross_up"] & data[s]["dv"] & (data[s]["stc"] > 50) for s in data}
    plain = {s: data[s]["cross_up"] for s in data}
    exit_ = {s: data[s]["cross_dn"] for s in data}

    def run(entry, start, **kw):
        eq, tr, ex = sim(tl, data, entry, exit_, start=start, **kw)
        return metrics(eq, tr, ex), eq

    print("--- 4h gated, 3-year window: risk x cap ---")
    for risk in (0.02, 0.03, 0.04, 0.05, 0.06):
        for cap in (0.5, 0.95):
            m, eq = run(gated, START, risk=risk, cap=cap)
            print("risk %4.1f%%  cap %4.2f   $%8.0f  %7.0f%%  dd %6.1f%%  sh %4.2f  n %3d  exp %4.0f%%" % (
                risk * 100, cap, 1000 * (1 + m["total"]), m["total"] * 100,
                m["dd"] * 100, m["sharpe"], m["trades"], m["exposure"] * 100))
    print()
    print("--- same, last 12 months ---")
    for risk in (0.03, 0.04, 0.05, 0.06):
        m, eq = run(gated, ONE_YEAR, risk=risk, cap=0.95)
        print("risk %4.1f%%  $%8.0f  %7.0f%%  dd %6.1f%%  sh %4.2f  n %3d" % (
            risk * 100, 1000 * (1 + m["total"]), m["total"] * 100,
            m["dd"] * 100, m["sharpe"], m["trades"]))
    print()
    print("--- reference: plain rule, same grid (3y) ---")
    for risk in (0.03, 0.04, 0.05):
        m, eq = run(plain, START, risk=risk)
        print("plain risk %4.1f%%  $%8.0f  %7.0f%%  dd %6.1f%%  sh %4.2f  n %3d" % (
            risk * 100, 1000 * (1 + m["total"]), m["total"] * 100,
            m["dd"] * 100, m["sharpe"], m["trades"]))



def meanrev():
    """Long-only dip buying: the other side of the trend book."""
    tl, data = build("4h")
    n = len(tl)
    close = pd.Series({s: data[s]["c"] for s in data}, index=tl).T

    def rsi(series, k=2):
        d = series.diff()
        up = d.clip(lower=0).ewm(alpha=1 / k, adjust=False, min_periods=k).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / k, adjust=False, min_periods=k).mean()
        rs = up / dn.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50)

    plans = {}
    for s in data:
        px = pd.Series(data[s]["c"], index=tl)
        sma20 = px.rolling(20, min_periods=20).mean()
        sd20 = px.rolling(20, min_periods=20).std()
        r2 = rsi(px, 2)
        r14 = rsi(px, 14)
        up_trend = px > pd.Series(data[s]["ema200"], index=tl)
        down3 = (px < px.shift(1)) & (px.shift(1) < px.shift(2)) & (px.shift(2) < px.shift(3))
        base = {
            "rsi2<10": (r2 < 10),
            "rsi2<10 + trend": (r2 < 10) & up_trend,
            "rsi2<10 + trend + gate": (r2 < 10) & up_trend & pd.Series(data[s]["dv"], index=tl) & (pd.Series(data[s]["stc"], index=tl) > 50),
            "bbc lower": (px < sma20 - 2 * sd20) & (r14 < 35),
            "bbc lower + trend": (px < sma20 - 2 * sd20) & (r14 < 35) & up_trend,
            "3 down closes + trend": down3 & up_trend & (r14 < 45),
        }
        for k, v in base.items():
            plans.setdefault(k, {})[s] = v.fillna(False).to_numpy(bool)
        exits = {
            "rsi2>60": (r2 > 60),
            "sma20": (px > sma20),
            "rsi2>75": (r2 > 75),
        }
        for k, v in exits.items():
            plans.setdefault("ex:" + k, {})[s] = v.fillna(False).to_numpy(bool)

    def run(entry, exit_, **kw):
        eq, tr, ex = sim(tl, data, entry, exit_, start=START, **kw)
        return metrics(eq, tr, ex), eq

    for name in ("rsi2<10", "rsi2<10 + trend", "rsi2<10 + trend + gate",
                 "bbc lower", "bbc lower + trend", "3 down closes + trend"):
        for ex_name in ("rsi2>60", "sma20"):
            m, eq = run(plans[name], plans["ex:" + ex_name], stop_atr=2.0, risk=0.02)
            line(name + " / exit " + ex_name, m, buckets(eq))


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "main"
    if mode == "robust":
        robust()
    elif mode == "recent":
        recent()
    elif mode == "placebo":
        placebo()
    elif mode == "trail":
        trail()
    elif mode == "sweep":
        sweep(tuple(sys.argv[2:]) or ("15m", "1h", "4h", "1d"))
    elif mode == "alt":
        alt_entries()
    elif mode == "risk":
        risk_grid()
    elif mode == "meanrev":
        meanrev()
    else:
        main()