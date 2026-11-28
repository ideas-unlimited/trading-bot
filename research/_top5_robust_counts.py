import pandas as pd
for f in ("research/top5_neighbors.csv","research/top5_alt_neighbors.csv"):
    d=pd.read_csv(f); d["worst"]=d[["train_ret","test_ret"]].min(axis=1)
    print(f)
    for sym,g in d.groupby("symbol"):
        ok=g[(g.train_ret>0)&(g.test_ret>0)]
        print(f"  {sym:10s} n={len(g):3d} robust={len(ok):3d} median_worst={ok.worst.median():+.2%} min={ok.worst.min():+.2%} max={ok.worst.max():+.2%}")
