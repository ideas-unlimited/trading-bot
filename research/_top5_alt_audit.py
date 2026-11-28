"""Audit alternate fifth-pick candidates (scratch)."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT/"src")); sys.path.insert(0,str(HERE/"cake")); sys.path.insert(0,str(HERE))
from bot.backtest import Engine,bars_to_frame
from bot.config import BotConfig,CostConfig,RiskConfig
from bot.scanlib import load_scan_bars
from bot.strategy import build
from cake_swing import SwingLab
cases=[
 ("LINK/USDT","1d","LINK capdip w10 d20 s1.5 rr2",build("capitulation_dip_uni",window=10,drop_pct=0.20,stop_atr=1.5,rr=2.0,max_hold=240)),
 ("BNB/USDT","1d","BNB momentum 120h t3.5",build("momentum_swing",lookback=120,regime_ma=480,atr_mult=2.5,trail_atr=3.5,breakout=0.02)),
 ("XRP/USDT","1d","XRP breakout 288h s2 t5",SwingLab(family="breakout",lookback_hours=288,trend_hours=480,stop_atr=2.0,rr=None,trail_atr=5.0,max_hold_hours=72)),
 ("AVAX/USDT","1d","AVAX breakout 288h s2 t3",SwingLab(family="breakout",lookback_hours=288,trend_hours=480,stop_atr=2.0,rr=None,trail_atr=3.0,max_hold_hours=72)),
]
for sym,tf,label,strat in cases:
    df=bars_to_frame(load_scan_bars(sym,tf,source="binance",days=740))
    cfg=BotConfig(initial_cash=1000.0,costs=CostConfig(fee_bps=10,slippage_bps=5),risk=RiskConfig(risk_per_trade=0.03,max_position_pct=0.95,max_drawdown_stop=0.99),bars_per_year=365.0*24.0/{"4h":4.0,"1d":24.0}[tf])
    eng=Engine(df,strat,cfg); eng.run(); res=eng.result(); tr=res.trades
    pnl=np.array([t.pnl for t in tr]); tot=pnl.sum(); top=np.sort(pnl)[::-1]
    eq=res.equity_curve; dd=(eq/eq.cummax()-1).min()
    print(f"\n{label}: n={len(tr)} ret={eq.iloc[-1]/1000-1:+.2%} dd={dd:.2%} pf={pnl[pnl>0].sum()/-pnl[pnl<0].sum():.2f} top1={top[0]/tot:.0%} top3={top[:3].sum()/tot:.0%} without_top1={(tot-top[0])/1000:+.2%}")
    for t in sorted(tr,key=lambda x:x.pnl,reverse=True)[:3]:
        print(f"  WIN {t.entry_ts.date()} pnl {t.pnl:+.0f} {t.reason_open[:40]}")
