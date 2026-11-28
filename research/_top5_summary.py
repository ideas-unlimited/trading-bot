import pandas as pd
from pathlib import Path
p = Path("research/top5_neighbors.csv")
d = pd.read_csv(p)
d["worst"] = d[["train_ret", "test_ret"]].min(axis=1)
for sym, g in d.groupby("symbol"):
    ok = g[(g.train_ret > 0) & (g.test_ret > 0)]
    print(f"\n== {sym} n={len(g)} robust={len(ok)} median_worst={ok.worst.median():+.2%} ==")
    cols = ["tf", "config", "train_ret", "train_dd", "train_trades", "test_ret", "test_dd", "test_trades", "test_pf", "full_ret", "full_dd", "worst"]
    print(ok.sort_values("worst", ascending=False)[cols].head(8).to_string(index=False))
print("\n== all robust sorted by worst ==")
ok = d[(d.train_ret > 0) & (d.test_ret > 0)]
print(ok.sort_values("worst", ascending=False)[["symbol", "tf", "config", "train_ret", "test_ret", "test_dd", "test_trades", "worst"]].head(25).to_string(index=False))
