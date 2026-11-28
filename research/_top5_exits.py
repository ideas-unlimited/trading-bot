"""Exit-rule ablation for the five finalists (scratch)."""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT/"src")); sys.path.insert(0,str(HERE/"cake")); sys.path.insert(0,str(HERE))
from bot.backtest import Engine,bars_to_frame
from bot.config import BotConfig,CostConfig,RiskConfig
from bot.scanlib import load_scan_bars
from bot.strategy import build
from cake_swing import SwingLab

BIG=10_000_000  # hours, effectively no time exit
CASES=[
 ("SUI", "SUI/USDT","4h",dict(family="breakout",lookback_hours=240,trend_hours=480,stop_atr=2.0)),
 ("XRP", "XRP/USDT","4h",None),
 ("INJ", "INJ/USDT","1d",dict(family="breakout",lookback_hours=168,trend_hours=480,stop_atr=1.75)),
 ("TAO", "TAO/USDT","1d",dict(family="breakout",lookback_hours=120,trend_hours=480,stop_atr=2.0)),
 ("AVAX","AVAX/USDT","1d",dict(family="breakout",lookback_hours=288,trend_hours=480,stop_atr=2.0)),
]
def run(sym,tf,strat):
    df=bars_to_frame(load_scan_bars(sym,tf,source="binance",days=740))
    cfg=BotConfig(initial_cash=1000.0,costs=CostConfig(fee_bps=10,slippage_bps=5),risk=RiskConfig(risk_per_trade=0.03,max_position_pct=0.95,max_drawdown_stop=0.99),bars_per_year=365.0*24.0/{"4h":4.0,"1d":24.0}[tf])
    eng=Engine(df,strat,cfg); eng.run(); res=eng.result(); eq=res.equity_curve
    return eq.iloc[-1]/1000-1, float((eq/eq.cummax()-1).min()), len(res.trades)
for name,sym,tf,kw in CASES:
    rows=[]
    if name=="XRP":
        for rr in (1.5,2.0,2.5,3.0):
            for hold in (120,240,480,BIG):
                if hold==BIG and rr!=2.0: continue
                s=build("wvfx_stoch",stop_atr=2.0,rr=rr,max_hold=hold,regime_days=200.0)
                rows.append((f"TP {rr:g}R hold {hold if hold!=BIG else 'none'}",*run(sym,tf,s)))
    else:
        variants=[
            ("trail + time",dict(rr=None,trail_atr=2.5 if name=="SUI" else 3.0,max_hold_hours=72)),
            ("trail, no time",dict(rr=None,trail_atr=2.5 if name=="SUI" else 3.0,max_hold_hours=BIG)),
            ("fixed 2R + time",dict(rr=2.0,trail_atr=None,max_hold_hours=72)),
            ("fixed 2R, no time",dict(rr=2.0,trail_atr=None,max_hold_hours=BIG)),
            ("stop+time only",dict(rr=None,trail_atr=None,max_hold_hours=72)),
            ("trail + 2R + time",dict(rr=2.0,trail_atr=2.5 if name=="SUI" else 3.0,max_hold_hours=72)),
        ]
        for label,extra in variants:
            s=SwingLab(**kw,**extra)
            rows.append((label,*run(sym,tf,s)))
    print(f"\n== {name} {tf} ==")
    for label,ret,dd,n in rows:
        print(f"  {label:22s} ret {ret:+7.2%} dd {dd:7.2%} n {n:2d}")
