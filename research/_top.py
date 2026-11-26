import pandas as pd
tr = pd.read_csv('runs/book3y/trades.csv', parse_dates=['entry_ts','exit_ts'])
tr['ret'] = tr.exit/tr.entry-1
cols = ['symbol','entry_ts','exit_ts','entry','exit','ret','r_multiple','pnl']
print(tr.sort_values('pnl', ascending=False).head(8)[cols].to_string(index=False))
print()
print('total pnl', tr.pnl.sum().round(1), 'top3 share', (tr.nlargest(3,'pnl').pnl.sum()/tr.pnl.sum()).round(2))
print('median pnl', tr.pnl.median().round(1))
print(tr.groupby('symbol').pnl.sum().round(0).to_dict())