import time, sys
sys.path.insert(0,'src')
import pandas as pd
from bot.backtest import Engine
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.strategy.momentum_swing import MomentumSwing
raw = pd.read_csv('data/crypto/BTCUSDT_4h.csv').tail(2600).reset_index(drop=True)
d = raw.rename(columns={'timestamp':'ts'})
d['ts'] = pd.to_datetime(d.ts, unit='s', utc=True)
cfg = BotConfig(initial_cash=1000, costs=CostConfig(fee_bps=10, slippage_bps=5), risk=RiskConfig(risk_per_trade=0.02, max_drawdown_stop=1.0))
t0=time.time()
for _ in range(5):
    e = Engine(d, MomentumSwing(), cfg); e.run(); r = e.result()
print('per run %.4fs  bars %d' % ((time.time()-t0)/5, len(d)))