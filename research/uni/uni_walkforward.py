
import sys, itertools, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import pandas as pd, numpy as np
from bot.scanlib import load_scan_bars
from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.strategy import CapitulationDipUni

B=load_scan_bars('UNI/USDT','4h',source='binance',days=730)
start=B[0].ts; mid=start+pd.Timedelta(days=365); end=B[-1].ts
tr=[x for x in B if x.ts<mid]; te=[x for x in B if x.ts>=mid]
def ev(bars,p):
    cfg=BotConfig(initial_cash=1000,costs=CostConfig(fee_bps=10,slippage_bps=5),risk=RiskConfig(risk_per_trade=0.05,max_position_pct=0.5,max_drawdown_stop=0.5),bars_per_year=2190)
    return run_backtest(bars,CapitulationDipUni(**p),cfg).metrics
rows=[]
for d,s,h,rr in itertools.product((0.18,0.19,0.20,0.21,0.22),(2.0,2.5,3.0),(12,15,18,21),(1.5,2.0,None)):
    p=dict(window=20,drop_pct=d,stop_atr=s,rr=rr,max_hold=h)
    a=ev(tr,p); b=ev(te,p)
    rows.append(dict(d=d,s=s,h=h,rr=rr,tr_ret=a['total_return'],tr_cal=a['calmar'],tr_n=a['trades'],te_ret=b['total_return'],te_cal=b['calmar'],te_n=b['trades'],te_dd=b['max_drawdown'],te_sh=b['sharpe']))
df=pd.DataFrame(rows)
df.to_csv(ROOT / 'research' / 'uni' / 'uni_walkforward.csv', index=False)
print('configs',len(df))
print('median train ret %.1f%%, median test ret %.1f%%'%(df.tr_ret.median()*100,df.te_ret.median()*100))
print('positive test: %d/%d (%.1f%%)'%((df.te_ret>0).sum(),len(df),(df.te_ret>0).mean()*100))
print('positive both: %d/%d'%(((df.te_ret>0)&(df.tr_ret>0)).sum(),len(df)))
from scipy.stats import spearmanr
print('rank corr train/test calmar: %.3f (p %.3g)'%spearmanr(df.tr_cal,df.te_cal))
print('rank corr train/test ret: %.3f'%spearmanr(df.tr_ret,df.te_ret).statistic)
elig=df[(df.tr_n>=6)&(df.tr_cal>-0.2)]
print('\nbest 5 by train Calmar:')
for _,r in elig.sort_values('tr_cal',ascending=False).head(5).iterrows():
    print('  d%.2f s%.1f h%2d rr%-4s | TRAIN ret %+6.1f%% cal %.2f n %2d | TEST ret %+6.1f%% dd %6.1f%% sh %.2f n %2d'%(r.d,r.s,r.h,str(r.rr),r.tr_ret*100,r.tr_cal,r.tr_n,r.te_ret*100,r.te_dd*100,r.te_sh,r.te_n))
print('\nbase d0.20 s2.5 h18 rrNone:')
for _,r in df[(df.d==0.20)&(df.s==2.5)&(df.h==18)&(df.rr.isna())].iterrows():
    print('  TRAIN ret %+6.1f%% cal %.2f n %2d | TEST ret %+6.1f%% dd %6.1f%% sh %.2f n %2d'%(r.tr_ret*100,r.tr_cal,r.tr_n,r.te_ret*100,r.te_dd*100,r.te_sh,r.te_n))
print('\ntop 10 by TEST (for inspection only):')
for _,r in df.sort_values('te_cal',ascending=False).head(10).iterrows():
    print('  d%.2f s%.1f h%2d rr%-4s | TEST ret %+6.1f%% cal %.2f | TRAIN ret %+6.1f%%'%(r.d,r.s,r.h,str(r.rr),r.te_ret*100,r.te_cal,r.tr_ret*100))
