import pandas as pd, numpy as np
for name in ('book3y','book1y'):
    e = pd.read_csv(f'runs/{name}/equity.csv', index_col=0, parse_dates=True).iloc[:,0]
    print(name, len(e), e.index[0], e.index[-1], f'{e.iloc[-1]/e.iloc[0]-1:.2%}')
    if name == 'book3y':
        y = e.groupby(e.index.year).agg(['first','last'])
        print((y['last']/y['first']-1).round(4).to_dict())
        m = e.resample('ME').last().pct_change().dropna()
        print('worst months', [(str(i)[:7], round(v*100,1)) for i,v in m.nsmallest(4).items()])
        print('best months', [(str(i)[:7], round(v*100,1)) for i,v in m.nlargest(3).items()])
tr = pd.read_csv('runs/book3y/trades.csv')
print(tr.reason.value_counts().to_dict())
print(tr.groupby('symbol').size().to_dict())
print('avg hold days', (pd.to_datetime(tr.exit_ts)-pd.to_datetime(tr.entry_ts)).dt.days.mean().round(1))
for tf in ('4h','1d'):
    d = pd.read_csv(f'runs/year_search_{tf}.csv')
    d = d[d.trades>=6]
    print(tf, 'n', len(d), 'median', round(d.ret.median(),4), 'p90', round(d.ret.quantile(0.9),4), 'max', round(d.ret.max(),4), 'positive', int((d.ret>0).sum()))