"""Cost stress on the five selected sleeves (scratch)."""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE / "cake"))
sys.path.insert(0, str(HERE))
from bot.backtest import Engine, bars_to_frame
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.scanlib import load_scan_bars
from bot.strategy import build
from cake_swing import SwingLab
from _top5_final import make
for fee, slip in ((10,5),(20,10),(30,15),(50,25)):
    rows=[]
    for sym, tf, label, strat in make():
        df=bars_to_frame(load_scan_bars(sym,tf,source="binance",days=740))
        cfg=BotConfig(initial_cash=1000.0,costs=CostConfig(fee_bps=fee,slippage_bps=slip),
                      risk=RiskConfig(risk_per_trade=0.03,max_position_pct=0.95,max_drawdown_stop=0.99),
                      bars_per_year=365.0*24.0/{"4h":4.0,"1d":24.0}[tf])
        eng=Engine(df,strat,cfg); eng.run(); res=eng.result(); eq=res.equity_curve
        dd=float((eq/eq.cummax()-1).min())
        rows.append((sym,tf,len(res.trades),eq.iloc[-1]/1000-1,dd))
    print(f"\n== costs fee={fee} slip={slip} ==")
    for sym,tf,n,r,dd in rows:
        print(f"  {sym:9s} {tf:2s} n={n:2d} ret {r:+.2%} dd {dd:.2%}")
