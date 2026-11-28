import pandas as pd, numpy as np
from pathlib import Path
p=Path("research")
files={"SUI":p/"final_SUI_4h_equity.csv","XRP":p/"final_XRP_4h_equity.csv","TAO":p/"final_TAO_1d_equity.csv","INJ":p/"final_INJ_1d_equity.csv","AVAX":p/"final_AVAX_1d_equity.csv"}
eq={k:pd.read_csv(f,index_col=0,parse_dates=True).iloc[:,0].resample("1D").last().ffill() for k,f in files.items()}
df=pd.DataFrame(eq).ffill().dropna()
def stats(s,name):
    r=s.pct_change().dropna()
    yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/s.iloc[0])**(1/yrs)-1
    dd=(s/s.cummax()-1).min()
    sharpe=r.mean()/r.std()*np.sqrt(365) if r.std() else np.nan
    monthly=s.resample("ME").last().pct_change().dropna()
    print(f"{name:8s} CAGR {cagr:+.2%} DD {dd:.2%} Sharpe {sharpe:.2f} months+ {(monthly>0).mean():.0%} worstM {monthly.min():.2%} bestM {monthly.max():.2%}")
for k in df: stats(df[k],k)
stats(df.sum(axis=1),"PORT")
print("\nmonthly combined:")
print((df.sum(axis=1).resample("ME").last().pct_change().dropna()*100).round(2).to_string())
