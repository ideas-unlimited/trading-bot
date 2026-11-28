
import sys
sys.path.insert(0, "research")
import numpy as np, pandas as pd
import lab
from lab import SYMBOLS

data = {s: lab.enrich(lab.load(s, "1d")).set_index("ts") for s in SYMBOLS}
R = pd.DataFrame({s: d["close"].pct_change() for s, d in data.items()}).fillna(0.0)
idx = R.index

SIG = {}
for s, d in data.items():
    SIG[("sma200", s)] = (d["close"] > d["close"].rolling(200).mean()).astype(float)
    SIG[("ema50_200", s)] = (d["ema50"] > d["ema200"]).astype(float)
    SIG[("mom60", s)] = (d["close"] > d["close"].shift(60)).astype(float)
    SIG[("mom120", s)] = (d["close"] > d["close"].shift(120)).astype(float)

def perf(r):
    r = r.dropna()
    eq = (1 + r).cumprod(); yrs = len(r) / 365.0
    vol = r.std() * np.sqrt(365.0)
    return {"CAGR": eq.iloc[-1] ** (1 / yrs) - 1, "Vol": vol,
            "Sharpe": (r.mean()*365)/vol if vol else 0,
            "MaxDD": (eq/eq.cummax()-1).min(), "Final": eq.iloc[-1]}

def round_trips(w, r):
    """Trade-level stats from a 0/1 exposure series (daily close rebalance)."""
    w = w.reindex(idx).fillna(0.0); r = r.reindex(idx).fillna(0.0)
    on = (w > 0).astype(int)
    switch = on.diff().fillna(on.iloc[0])
    entries = np.where(switch > 0)[0]; exits = np.where(switch < 0)[0]
    trades, start = [], None
    for i in range(len(on)):
        if switch.iloc[i] > 0: start = i
        elif switch.iloc[i] < 0 and start is not None:
            trades.append(((1+r.iloc[start:i+1]).prod() - 1)); start = None
    if start is not None: trades.append((1+r.iloc[start:]).prod() - 1)
    t = np.array(trades)
    if len(t) == 0: return 0, np.nan, np.nan
    return len(t), float((t > 0).mean()), float(t.mean())

for name in ("sma200", "ema50_200", "mom60", "mom120"):
    Wm = pd.DataFrame({s: SIG[(name, s)] for s in SYMBOLS}).reindex(idx).fillna(0.0).shift(1).fillna(0.0)
    port = (Wm * R).sum(axis=1) / len(SYMBOLS)
    print(f"\n{name}: equal capital per coin, daily rebalance")
    allt, allw = [], []
    for s in SYMBOLS:
        p = perf(Wm[s] * R[s]); n, wr, avg = round_trips(Wm[s], R[s])
        if p: print(f"   {s:9s} CAGR {p['CAGR']*100:6.1f}%  Sh {p['Sharpe']:5.2f}  DD {p['MaxDD']*100:6.1f}%  expo {Wm[s].mean()*100:5.1f}%  trades {n:3d} win {wr*100:4.0f}% avg {avg*100:+.1f}%")
    p = perf(port)
    print(f"   {'PORTFOLIO':9s} CAGR {p['CAGR']*100:6.1f}%  Sh {p['Sharpe']:5.2f}  DD {p['MaxDD']*100:6.1f}%  Final x{p['Final']:.2f}")
    y = (1+port).groupby(port.index.year).prod() - 1
    print("   yearly: " + "  ".join(f"{k}:{v*100:+.0f}%" for k, v in y.items()))
