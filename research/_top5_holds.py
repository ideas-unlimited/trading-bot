import pandas as pd
for f in ("research/top5_neighbors.csv","research/top5_alt_neighbors.csv"):
    d=pd.read_csv(f)
    print("\n==",f)
    for sym in ("SUI/USDT","TAO/USDT","INJ/USDT","AVAX/USDT"):
        g=d[d.symbol==sym]
        if not len(g): continue
        print("\n",sym,"holds:",sorted(g.config.str.extract(r"h(\d+)$")[0].dropna().unique()))
        # show all rows with central-ish params
        for cfg in g.config:
            if ("120h s2 t3" in cfg or "168h s1.75 t3" in cfg or "240h s2 t2.5" in cfg or "288h s2 t3" in cfg):
                r=g[g.config==cfg].iloc[0]
                print(f"  {cfg:32s} train {r.train_ret:+.2%}/{r.train_dd:.1%} test {r.test_ret:+.2%}/{r.test_dd:.1%} n {r.train_trades}/{r.test_trades}")
