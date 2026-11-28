import sys; sys.path.insert(0,'src'); sys.path.insert(0,'research')
import numpy as np, pandas as pd
from bot.backtest import Engine
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.strategy.momentum_swing import MomentumSwing
from bot import indicators as ind
from fastsim import simulate

def frame(path, n):
    d = pd.read_csv(path).tail(n).reset_index(drop=True).rename(columns={'timestamp':'ts'})
    d['ts'] = pd.to_datetime(d.ts, unit='s', utc=True)
    return d

cfg = BotConfig(initial_cash=1000, costs=CostConfig(fee_bps=10, slippage_bps=5),
                risk=RiskConfig(risk_per_trade=0.02, max_drawdown_stop=1.0))

for path, n in [('data/crypto/BTCUSDT_4h.csv',2600), ('data/crypto/ETHUSDT_1d.csv',800), ('data/crypto/SOLUSDT_4h.csv',2600)]:
    d = frame(path, n)
    strat = MomentumSwing()
    e = Engine(d, strat, cfg); e.run(); res = e.result()
    p = strat.prepare(d)
    close = p.close.to_numpy(float)
    atr = p.atr.to_numpy(float)
    regime = p.regime.to_numpy(float)
    mom = p.mom.to_numpy(float)
    hi20 = p.hi20.to_numpy(float)
    entry = (close > regime) & (mom > 0) & ((1 - close/hi20) <= 0.02)
    entry = np.nan_to_num(entry.astype(bool)) & np.isfinite(atr) & np.isfinite(regime)
    exitg = np.zeros(len(close), bool)
    eq, trades, bars = simulate(p.open.to_numpy(float), p.high.to_numpy(float), p.low.to_numpy(float),
                                close, atr, entry, exitg, trail_k=3.5, stop_atr=2.5)
    print(path.split('/')[-1], 'engine %.2f  fast %.2f  trades %d/%d' % (res.final_equity, eq[-1], len(res.trades), len(trades)))