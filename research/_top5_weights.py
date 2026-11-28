import pandas as pd
from pathlib import Path
p=Path("research")
files={"SUI":p/"final_SUI_4h_equity.csv","XRP":p/"final_XRP_4h_equity.csv","TAO":p/"final_TAO_1d_equity.csv","INJ":p/"final_INJ_1d_equity.csv","AVAX":p/"final_AVAX_1d_equity.csv"}
eq={k:pd.read_csv(f,index_col=0,parse_dates=True).iloc[:,0].resample("1D").last().ffill() for k,f in files.items()}
df=pd.DataFrame(eq).ffill().dropna()
for name,w in {"equal":{k:0.2 for k in df},"confidence":{ "SUI":0.30,"XRP":0.25,"INJ":0.20,"TAO":0.15,"AVAX":0.10},"conservative":{"SUI":0.25,"XRP":0.30,"INJ":0.20,"TAO":0.15,"AVAX":0.10}}.items():
    port=sum(df[k]*w[k] for k in df)
    ret=port.iloc[-1]/1000-1; dd=(port/port.cummax()-1).min()
    y1=port[(port.index>="2024-11-28")&(port.index<"2025-11-28")]
    y2=port[(port.index>="2025-11-28")]
    print(f"{name:12s} ret {ret:+.2%} dd {dd:.2%} y1 {y1.iloc[-1]/y1.iloc[0]-1:+.2%} y2 {y2.iloc[-1]/y2.iloc[0]-1:+.2%}")
