"""JUP next-15m-demand-zone target / trail study (research only).

Window: last 12 months of 15m bars (sub-4h hard rule), Binance spot.
Signals from confirmed 4h/6h/1d demand clusters; 3 DCA tranches at
upper/center/lower.  Target = nearest confirmed 15m demand band above
the signal close.  Execution is on 15m bars.  Costs: 10 bps fee +
5 bps slippage per side.  Signals fill on a later 15m bar; no same-bar
target or trail after a fresh fill.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from bot.analysis import atr
from bot.backtest import bars_to_frame
from bot.scanlib import load_scan_bars
from research.jup import jup_liquidity as jl

jl.TF_HOURS["15m"] = 0.25

FEE = 0.0010
SLIP = 0.0005


def load(tf: str, days: int = 365) -> pd.DataFrame:
    return bars_to_frame(
        load_scan_bars("JUP/USDT", tf, source="binance", days=days)
    ).set_index("ts")


def build_signals(d4, zones, z15, *, stop_atr: float = 1.0):
    a4 = atr(d4["high"], d4["low"], d4["close"], 14).to_numpy(float)
    states = [False] * len(zones)
    out = []
    for i, ts in enumerate(d4.index):
        bc = ts + pd.Timedelta(hours=4)
        c = float(d4["close"].iloc[i])
        for zi, z in enumerate(zones):
            if states[zi]:
                continue
            if bc >= z.expires_at:
                states[zi] = True
            elif bc >= z.available_at and c < z.lower - jl.ZONE_BREAK_ATR * a4[i]:
                states[zi] = True
        clusters = jl.active_clusters(
            zones, states, bc, c, a4[i], max_dist_atr=3.0,
            min_width_atr=0.15, above="upper",
        )
        if not clusters:
            continue
        clusters.sort(key=lambda x: (len(x.tfs), -((c - x.upper) / a4[i])), reverse=True)
        cl = clusters[0]
        e1 = cl.upper
        stop = cl.lower - stop_atr * a4[i]
        if stop <= 0 or e1 <= stop:
            continue
        cand = [
            z for z in z15
            if z.available_at <= bc < z.expires_at
            and z.lower > c * 1.002
            and z.lower > e1 * 1.002
        ]
        if not cand:
            continue
        z = min(cand, key=lambda x: x.lower)
        target = z.lower
        target_c = z.center
        if target <= e1 or target_c <= e1:
            continue
        out.append({
            "ts": bc, "e1": e1, "lower": cl.lower, "stop": stop,
            "conf": len(cl.tfs), "tfs": tuple(sorted(cl.tfs)),
            "target": target, "target_c": target_c,
            "rr": (target - e1) / (e1 - stop),
            "rr_c": (target_c - e1) / (e1 - stop),
        })
    return out


def run(signals, d15, *, min_conf, max_conf, mode, side, trail_mult,
        tranches, max_hold_bars, min_rr=0.0, partial_frac=0.5,
        entry_window_bars=288, fee=FEE, slip=SLIP):
    atr15 = atr(d15["high"], d15["low"], d15["close"], 14).to_numpy(float)
    o = d15["open"].to_numpy(float); h = d15["high"].to_numpy(float)
    l = d15["low"].to_numpy(float); c = d15["close"].to_numpy(float)
    sigs = [
        s for s in signals
        if s["conf"] >= min_conf and (max_conf is None or s["conf"] <= max_conf)
        and s["rr" if side == "lower" else "rr_c"] >= min_rr
    ]
    sig_i = 0
    setup = None
    trades = []

    for i, ts in enumerate(d15.index):
        while sig_i < len(sigs) and sigs[sig_i]["ts"] <= ts:
            s = sigs[sig_i]
            sig_i += 1
            if setup is None:
                target = s["target"] if side == "lower" else s["target_c"]
                rr = s["rr"] if side == "lower" else s["rr_c"]
                if rr < min_rr:
                    continue
                levels = [s["e1"]]
                if tranches >= 2:
                    levels.append(0.5 * (s["e1"] + s["lower"]))
                if tranches >= 3:
                    levels.append(s["lower"])
                setup = {
                    "arm_i": i + 1, "expires_i": i + 1 + entry_window_bars,
                    "levels": levels, "filled": [False] * len(levels),
                    "fills": 0, "qty": 0.0, "cost": 0.0, "risk": 0.0,
                    "avg": 0.0, "first_i": None, "entry_ts": ts,
                    "stop": s["stop"], "target": target, "rr": rr,
                    "conf": s["conf"], "tfs": s["tfs"],
                    "mode": mode, "trail_mult": trail_mult,
                    "target_hit": False, "trail_active": mode == "trail_only",
                    "highest": 0.0, "trail": s["stop"], "accepting": True,
                    "realized": 0.0,
                }

        def sell(q, px):
            q = min(q, setup["qty"])
            if q <= 0:
                return
            px = max(px * (1.0 - slip), 1e-12)
            proceeds = q * px
            fee_paid = proceeds * fee
            cost_part = setup["cost"] * (q / setup["qty"])
            setup["realized"] += proceeds - fee_paid - cost_part
            setup["cost"] -= cost_part
            setup["qty"] -= q
            if setup["qty"] <= 1e-12:
                setup["qty"] = 0.0

        def record(ts, reason):
            nonlocal setup
            if setup is None:
                return
            pnl = setup["realized"]
            risk = max(setup["risk"], 1e-12)
            trades.append({
                "entry_ts": setup["entry_ts"], "exit_ts": ts,
                "fills": setup["fills"], "pnl": pnl,
                "r": pnl / risk, "reason": reason,
                "target_hit": setup["target_hit"], "conf": setup["conf"],
                "tfs": setup["tfs"], "rr": setup["rr"],
            })
            setup = None

        def close_all(px, reason):
            if setup is None or setup["qty"] <= 0:
                return
            sell(setup["qty"], px)
            record(ts, reason)

        if setup is not None and setup["qty"] > 0:
            if o[i] <= setup["stop"]:
                close_all(o[i], "stop_gap")
            elif setup["mode"] == "hard" and o[i] >= setup["target"]:
                setup["target_hit"] = True
                close_all(o[i], "target_gap")
            elif setup["trail_active"] and o[i] <= setup["trail"]:
                close_all(o[i], "trail_gap")
        if setup is not None and setup["qty"] > 0:
            if l[i] <= setup["stop"]:
                close_all(setup["stop"], "stop")
            elif setup["mode"] == "hard" and h[i] >= setup["target"]:
                setup["target_hit"] = True
                close_all(setup["target"], "target")
            elif setup["mode"] == "partial" and not setup["target_hit"] and h[i] >= setup["target"]:
                sell(setup["qty"] * partial_frac, setup["target"])
                setup["target_hit"] = True
                setup["trail_active"] = True
                setup["accepting"] = False
                if setup["qty"] <= 0:
                    record(ts, "target_partial")
            elif setup["mode"] == "trail_after" and not setup["target_hit"] and h[i] >= setup["target"]:
                setup["target_hit"] = True
                setup["trail_active"] = True
                setup["accepting"] = False
            elif setup["trail_active"] and l[i] <= setup["trail"]:
                close_all(setup["trail"], "trail")
            elif setup["first_i"] is not None and i - setup["first_i"] >= max_hold_bars:
                close_all(c[i], "time")

        if setup is not None and setup["accepting"] and i >= setup["arm_i"]:
            if setup["qty"] == 0 and i > setup["expires_i"]:
                setup = None
            elif setup is not None:
                if setup["qty"] == 0 and o[i] <= setup["stop"]:
                    setup = None
                else:
                    for k, lev in enumerate(setup["levels"]):
                        if setup["filled"][k] or l[i] > lev:
                            continue
                        raw = min(o[i], lev)
                        buy = raw * (1.0 + slip)
                        q = 1.0 / buy
                        fee_paid = q * buy * fee
                        old = setup["qty"]
                        setup["qty"] += q
                        setup["avg"] = (setup["avg"] * old + buy * q) / setup["qty"]
                        setup["cost"] += q * buy + fee_paid
                        setup["risk"] += q * (buy - setup["stop"])
                        setup["filled"][k] = True
                        setup["fills"] += 1
                        if setup["first_i"] is None:
                            setup["first_i"] = i
                            setup["entry_ts"] = ts
                    if setup is not None and setup["qty"] > 0:
                        if l[i] <= setup["stop"]:
                            close_all(setup["stop"], "stop_same_bar")
                        elif i - setup["first_i"] >= max_hold_bars:
                            close_all(c[i], "time")

        if setup is not None and setup["qty"] > 0:
            setup["highest"] = max(setup["highest"], h[i])
            if setup["trail_active"]:
                new_trail = setup["highest"] - setup["trail_mult"] * atr15[i]
                if new_trail > setup["trail"]:
                    setup["trail"] = new_trail

    if setup is not None and setup["qty"] > 0:
        close_all(c[-1], "end")
    return trades


def summarize(tr):
    if not tr:
        return {}
    n = len(tr)
    wins = [t["pnl"] for t in tr if t["pnl"] > 0]
    losses = [t["pnl"] for t in tr if t["pnl"] < 0]
    total_r = sum(t["r"] for t in tr)
    pnls = sorted([t["pnl"] for t in tr], reverse=True)
    return {
        "n": n, "win": 100 * len(wins) / n,
        "pf": sum(wins) / abs(sum(losses)) if losses else float("inf"),
        "totalR": total_r, "avgR": total_r / n,
        "avg_fills": np.mean([t["fills"] for t in tr]),
        "target_hit": 100 * sum(t["target_hit"] for t in tr) / n,
        "top3_pnl": sum(pnls[:3]),
        "top3_share": sum(pnls[:3]) / sum(pnls) if sum(pnls) > 0 else float("nan"),
        "best": pnls[0], "worst": pnls[-1],
    }


def line(name, tr):
    s = summarize(tr)
    if not s:
        return f"{name:<30} n 0"
    return (f"{name:<30} n {s['n']:>3} win {s['win']:>5.1f}% "
            f"fills {s['avg_fills']:.2f} hit {s['target_hit']:>5.1f}% "
            f"PF {s['pf']:>4.2f} totalR {s['totalR']:>7.1f} "
            f"avgR {s['avgR']:>6.2f} top3 {s['top3_share']*100:>5.0f}%")


def main():
    d4, d6, d1 = load("4h"), load("6h"), load("1d")
    end = min(x.index[-1] for x in (d4, d6, d1))
    d4, d6, d1 = [x.loc[x.index < end].copy() for x in (d4, d6, d1)]
    d15 = load("15m")
    d15 = d15.loc[d15.index <= end + pd.Timedelta(days=1)].copy()
    z15 = jl.build_zones(d15, "15m", swing=5, half_atr=0.50, max_age_days=3)
    zones = jl.build_zones(d4, "4h") + jl.build_zones(d6, "6h") + jl.build_zones(d1, "1d")
    sigs = build_signals(d4, zones, z15)
    print("window", d4.index[0], "->", d4.index[-1], "15m", d15.index[0], "->", d15.index[-1])
    print("signals with 15m target", len(sigs), "of", len(d4), "| 4h bars", len(d4))
    print("target RR (lower) pct", np.round(np.percentile([s['rr'] for s in sigs], [10, 25, 50, 75, 90]), 2))
    print()
    for mode, tm in [("hard", 0), ("partial", 1.5), ("partial", 2.0), ("partial", 2.5),
                     ("trail_after", 1.5), ("trail_after", 2.0), ("trail_after", 2.5),
                     ("trail_only", 1.5), ("trail_only", 2.0)]:
        tr = run(sigs, d15, min_conf=2, max_conf=None, mode=mode, side="lower",
                 trail_mult=tm, tranches=3, max_hold_bars=480)
        print(line(f"{mode} trail{tm} >=2", tr))
    print()
    for mc, xc in [(1, 1), (2, 2), (3, 3)]:
        tr = run(sigs, d15, min_conf=mc, max_conf=xc, mode="trail_after", side="lower",
                 trail_mult=1.5, tranches=3, max_hold_bars=480)
        print(line(f"trail_after1.5 exact{mc}", tr))
    print()
    for side in ("lower", "center"):
        for mode in ("hard", "trail_after"):
            tr = run(sigs, d15, min_conf=2, max_conf=None, mode=mode, side=side,
                     trail_mult=1.5, tranches=3, max_hold_bars=480)
            print(line(f"{mode} target {side}", tr))
    print()
    for mrr in (0.0, 0.75, 1.0, 1.5, 2.0):
        tr = run(sigs, d15, min_conf=2, max_conf=None, mode="hard", side="lower",
                 trail_mult=0, tranches=3, max_hold_bars=480, min_rr=mrr)
        print(line(f"hard minRR {mrr:.2f}", tr))


if __name__ == "__main__":
    main()
