import pandas as pd
from pathlib import Path
import numpy as np
p = Path("research")
files = {
    "SUI": p/"final_SUI_4h_equity.csv",
    "XRP": p/"final_XRP_4h_equity.csv",
    "TAO": p/"final_TAO_1d_equity.csv",
    "INJ": p/"final_INJ_1d_equity.csv",
    "AVAX": p/"final_AVAX_1d_equity.csv",
}
eq = {}
for k, f in files.items():
    d = pd.read_csv(f, index_col=0, parse_dates=True).iloc[:, 0]
    d = d[~d.index.duplicated()].sort_index()
    eq[k] = d.resample("1D").last().ffill()
df = pd.DataFrame(eq).ffill().dropna()
print("window", df.index[0], df.index[-1], "days", len(df))
print("\nfinal equity:")
for k in df:
    print(f"  {k}: {df[k].iloc[-1]:.2f} ({(df[k].iloc[-1]/1000-1):+.2%})")
print("\ncorrelation of daily returns:")
ret = df.pct_change().dropna()
print(ret.corr().round(2).to_string())
port = df.sum(axis=1)
print(f"\ncombined 5x1000 final {port.iloc[-1]:.2f} ret {port.iloc[-1]/5000-1:+.2%}")
dd = (port/port.cummax()-1)
print(f"combined maxDD {dd.min():.2%}")
for a,b in (("2024-11-28","2025-11-28"),("2025-11-28","2026-11-28")):
    seg=port[(port.index>=a)&(port.index<b)]
    print(a,b,f"ret {seg.iloc[-1]/seg.iloc[0]-1:+.2%} dd {(seg/seg.cummax()-1).min():.2%}")
print("\nyearly per sleeve:")
for k in df:
    s=df[k]
    for a,b in (("2024-11-28","2025-11-28"),("2025-11-28","2026-11-28")):
        seg=s[(s.index>=a)&(s.index<b)]
        print(f"  {k} {a[:7]} {seg.iloc[-1]/seg.iloc[0]-1:+.2%}")
print("\navg pairwise corr", ret.corr().values[np.triu_indices(5,1)].mean().round(3))
