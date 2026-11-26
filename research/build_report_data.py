import sys, json, math
sys.path.insert(0, 'src'); sys.path.insert(0, 'research')
import numpy as np, pandas as pd
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.backtest import run_backtest
from bot.data.feed import load_csv
from bot.strategy.momentum_swing import MomentumSwing
import lab
from lab import SYMBOLS

out = {}
# --- per coin single-asset swing, 1k, 2% risk, zero fees ---
rows = []
for s in SYMBOLS:
    bars = load_csv(f'data/crypto/{s}_1d.csv')
    strat = MomentumSwing()
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(fee_bps=0.0, slippage_bps=0.0),
                    risk=RiskConfig(risk_per_trade=0.02, max_position_pct=0.95, max_drawdown_stop=0.25),
                    bars_per_year=365.0)
    res = run_backtest(bars, strat, cfg)
    m = res.metrics
    rows.append({'sym': s.replace('USDT',''), 'cagr': m['cagr'], 'sharpe': m['sharpe'], 'dd': m['max_drawdown'],
                 'trades': int(m['trades']), 'win': m['win_rate'], 'pf': m['profit_factor'], 'final': res.final_equity,
                 'expo': m['exposure']})
out['per_coin'] = rows

# --- rotation risk variants ---
from bot.portfolio import RotationConfig, simulate_rotation, summarize, load_closes_from_csv
closes = load_closes_from_csv('data/crypto', SYMBOLS, '1d')
variants = []
for vt in (0.25, 0.40, 0.60):
    for top in (1, 2, 3):
        res = simulate_rotation(closes, RotationConfig(top_n=top, vol_target=vt, fee_bps=10, initial_cash=1000.0))
        s = summarize(res)
        variants.append({'vol_target': vt, 'top': top, 'cagr': s['cagr'], 'sharpe': s['sharpe'], 'dd': s['max_drawdown'],
                         'final': s['final'], 'expo': s['exposure'], 'trades': s['trades'], 'win': s['win_rate'],
                         'yearly': {str(k): float(v) for k, v in res.yearly.items()}})
out['variants'] = variants

# main backtest curve for the chart
res = simulate_rotation(closes, RotationConfig(top_n=2, vol_target=0.40, fee_bps=10, initial_cash=1000.0))
out['main'] = {'summary': summarize(res), 'yearly': {str(k): float(v) for k, v in res.yearly.items()}}
eq = res.equity
out['curve'] = {'ts': [d.strftime('%Y-%m-%d') for d in eq.index], 'eq': [float(x) for x in eq.values]}
btc = closes['BTCUSDT'].reindex(eq.index).ffill()
out['btc'] = [float(x) for x in (btc / btc.iloc[0]).values]

# geometry table: win rate vs expectancy at different payoffs (trend+breakout universe)
geom = []
for stop, rr in ((2.0,1.5),(2.0,1.0),(2.5,0.75),(2.5,0.5),(3.0,0.4)):
    d = lab.build('1d', stop_mult=stop, rr=rr, horizon=20)
    p = pd.concat([x.assign(sym=s) for s, x in d.items()], ignore_index=True).dropna(subset=['y'])
    q = p[(p.px_ema50 > 0) & (p.btc_regime > 0) & (p.dist_hi20 > -0.02)]
    geom.append({'stop': stop, 'rr': rr, 'be': 1/(1+rr), 'win': float(q.y.mean()), 'ev': float(q.r_mult.mean()),
                 'n': int(len(q)), 'win_lo': float(q.y.mean() - 1.96*math.sqrt(q.y.mean()*(1-q.y.mean())/len(q)))})
out['geometry'] = geom

# calibration evidence: 27-feature vs 5-feature model AUC (from earlier runs, recomputed)
import model
p = model.panel(lab.build('1d', stop_mult=2.0, rr=1.5, horizon=40), '1d')
w = model.walk_forward(p, '1d', horizon=40)
out['model_a'] = {'auc': model.auc(w.y, w.p), 'base': float(w.y.mean()), 'n': int(len(w)),
                  'rel': model.reliability(w, 6).round(3).to_dict('records')}
p2 = model.panel(lab.build('1d', stop_mult=3.0, rr=0.4, horizon=20), '1d')
p2 = p2[(p2.px_ema50 > 0) & (p2.btc_regime > 0) & (p2.dist_hi20 > -0.02)]
w2 = model.walk_forward(p2, '1d', horizon=20, features=['px_ema50','dist_hi20','atr_pct','btc_regime','r20','z20','rsi'], l2=3.0)
out['model_b'] = {'auc': model.auc(w2.y, w2.p), 'base': float(w2.y.mean()), 'n': int(len(w2)),
                  'rel': model.reliability(w2, 5).round(3).to_dict('records')}

json.dump(out, open('runs/report_data.json','w'), indent=1)
print('rotation:', {k: round(v,3) for k, v in out['main']['summary'].items()})
print('per coin:'); [print(' ', r) for r in rows]
print('geometry:'); [print(' ', g) for g in geom]
print('model AUC A/B:', round(out['model_a']['auc'],3), round(out['model_b']['auc'],3))
