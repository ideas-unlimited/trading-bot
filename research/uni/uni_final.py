
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import pandas as pd, numpy as np
from bot.scanlib import load_scan_bars
from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.strategy import CapitulationDipUni

B=load_scan_bars('UNI/USDT','4h',source='binance',days=730)
P=dict(window=20,drop_pct=0.20,stop_atr=2.5,rr=1.5,max_hold=18)
def run(bars=B,p=P,risk=0.05,cap=0.5,fee=10,slip=5,ddstop=0.5):
    cfg=BotConfig(initial_cash=1000,costs=CostConfig(fee_bps=fee,slippage_bps=slip),risk=RiskConfig(risk_per_trade=risk,max_position_pct=cap,max_drawdown_stop=ddstop),bars_per_year=2190)
    return run_backtest(bars,CapitulationDipUni(**p),cfg)
def d(m):
    return dict(ret=round(m['total_return']*100,2),dd=round(m['max_drawdown']*100,2),sharpe=round(m['sharpe'],3),trades=int(m['trades']),win=round(m['win_rate']*100,2),avg=round(m['avg_trade']*100,2),pf=round(m['profit_factor'],2) if m['profit_factor']==m['profit_factor'] else None,exposure=round(m['exposure']*100,2))

start=B[0].ts; end=B[-1].ts; mid=start+pd.Timedelta(days=365)
out={}
out['window']=[str(start),str(end)]; out['bars']=len(B)
out['full']=d(run().metrics)
out['train']=d(run([x for x in B if x.ts<mid]).metrics)
out['test']=d(run([x for x in B if x.ts>=mid]).metrics)
out['folds']=[]
for k in range(4):
    a=start+pd.Timedelta(days=182*k); b=start+pd.Timedelta(days=182*(k+1)) if k<3 else end
    out['folds'].append([str(a.date()),str(b.date()),d(run([x for x in B if a<=x.ts<b]).metrics)])
out['risk']=[]
for risk in (0.02,0.03,0.05,0.075,0.10):
    out['risk'].append([risk,d(run(risk=risk).metrics)])
out['costs']=[]
for fee,slip in ((10,5),(20,10),(30,15),(50,25),(100,50)):
    out['costs'].append([fee,slip,d(run(fee=fee,slip=slip).metrics)])

r=run()
eq=r.equity_curve
trades=[]
for t in r.trades:
    e=eq[eq.index<=t.entry_ts].iloc[-1]
    hold=(t.exit_ts-t.entry_ts).total_seconds()/14400
    trades.append(dict(entry=str(t.entry_ts),exit=str(t.exit_ts),hold=round(hold,1),ret=round(t.return_pct*100,2),account=round(t.pnl/e*100,2),reason=t.reason_close))
out['trades']=trades
R=np.array([t['account']/100 for t in trades])
rng=np.random.default_rng(42)
boot=np.array([rng.choice(R,size=len(R),replace=True).sum() for _ in range(20000)])
out['bootstrap']=dict(mean=float(boot.mean()*100),p5=float(np.percentile(boot,5)*100),p1=float(np.percentile(boot,1)*100),p_le0=float((boot<=0).mean()))
c=pd.Series([b.close for b in B],index=[b.ts for b in B])
fwd=(c.shift(-18)/c-1).dropna()
out['unconditional']=dict(mean=float(fwd.mean()*100),win=float((fwd>0).mean()*100))
rng=np.random.default_rng(7); means=[]
for _ in range(20000): means.append(fwd.iloc[rng.integers(0,len(fwd),size=len(R))].mean())
means=np.array(means)
out['placebo']=dict(mean=float(means.mean()*100),p95=float(np.percentile(means,95)*100),p_ge=float((means>=R.mean()).mean()))
print(json.dumps(out,indent=2))
