"""Can a tighter trail replace the time exit? (scratch)"""
from __future__ import annotations
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT/"src")); sys.path.insert(0,str(HERE/"cake")); sys.path.insert(0,str(HERE))
from bot.backtest import Engine,bars_to_frame
from bot.config import BotConfig,CostConfig,RiskConfig
from bot.scanlib import load_scan_bars
from cake_swing import SwingLab
BIG=10_000_000
CASES=[("SUI","SUI/USDT","4h",240,2.0),("INJ","INJ/USDT","1d",168,1.75),("TAO","TAO/USDT","1d",120,2.0),("AVAX","AVAX/USDT","1d",288,2.0)]
for name,sym,tf,lb,stop in CASES:
    df=bars_to_frame(load_scan_bars(sym,tf,source="binance",days=740))
    print(f"\n== {name} ==")
    for trail in (1.0,1.5,2.0,2.5,3.0):
        for hold,label in ((BIG,"no time"),(72,"time 72")):
            s=SwingLab(family="breakout",lookback_hours=lb,trend_hours=480,stop_atr=stop,rr=None,trail_atr=trail,max_hold_hours=hold)
            cfg=BotConfig(initial_cash=1000.0,costs=CostConfig(fee_bps=10,slippage_bps=5),risk=RiskConfig(risk_per_trade=0.03,max_position_pct=0.95,max_drawdown_stop=0.99),bars_per_year=365.0*24.0/{"4h":4.0,"1d":24.0}[tf])
            eng=Engine(df,s,cfg); eng.run(); res=eng.result(); eq=res.equity_curve
            ret=eq.iloc[-1]/1000-1; dd=float((eq/eq.cummax()-1).min())
            print(f"  trail {trail:3.1f} {label:8s} ret {ret:+7.2%} dd {dd:7.2%} n {len(res.trades):2d}")
