import pandas as pd, numpy as np
from pathlib import Path
p=Path("research")
files={"SUI":p/"final_SUI_4h_equity.csv","XRP":p/"final_XRP_4h_equity.csv","TAO":p/"final_TAO_1d_equity.csv","INJ":p/"final_INJ_1d_equity.csv","AVAX":p/"final_AVAX_1d_equity.csv"}
eq={k:pd.read_csv(f,index_col=0,parse_dates=True).iloc[:,0].resample("1D").last().ffill() for k,f in files.items()}
df=pd.DataFrame(eq).ffill().dropna()
w={"SUI":0.25,"XRP":0.30,"INJ":0.20,"TAO":0.15,"AVAX":0.10}
port=sum(df[k]*w[k] for k in df)
r=port.pct_change().dropna(); yrs=(port.index[-1]-port.index[0]).days/365.25
cagr=(port.iloc[-1]/port.iloc[0])**(1/yrs)-1
dd=(port/port.cummax()-1).min()
sh=r.mean()/r.std()*np.sqrt(365)
m=port.resample("ME").last().pct_change().dropna()
print(f"weighted CAGR {cagr:+.2%} DD {dd:.2%} Sharpe {sh:.2f} months+ {(m>0).mean():.0%} worst {m.min():.2%}")
