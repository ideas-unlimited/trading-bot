"""UNI capitulation sleeve: does trailing beat the fixed 1.5R exit?"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

import pandas as pd

from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.scanlib import load_scan_bars
from bot.strategy import CapitulationDipUni

BARS = load_scan_bars('UNI/USDT', '4h', source='binance', days=730)
START = BARS[0].ts
MID = START + pd.Timedelta(days=365)
TRAIN = [b for b in BARS if b.ts < MID]
TEST = [b for b in BARS if b.ts >= MID]


def run(bars, rr, trail, hold, after=0.0):
    params = dict(window=20, drop_pct=0.20, stop_atr=2.5, rr=rr, max_hold=hold)
    if trail is not None:
        params['trail_atr'] = trail
        params['trail_after_r'] = after
    cfg = BotConfig(
        initial_cash=1000,
        costs=CostConfig(fee_bps=10, slippage_bps=5),
        risk=RiskConfig(risk_per_trade=0.05, max_position_pct=0.5,
                        max_drawdown_stop=0.5),
        bars_per_year=2190,
    )
    return run_backtest(bars, CapitulationDipUni(**params), cfg)


def m(res):
    x = res.metrics
    return dict(
        ret=round(x['total_return'] * 100, 2),
        dd=round(x['max_drawdown'] * 100, 2),
        sharpe=round(x['sharpe'], 3),
        trades=int(x['trades']),
        win=round(x['win_rate'] * 100, 2),
        avg=round(x['avg_trade'] * 100, 2),
        exposure=round(x['exposure'] * 100, 2),
    )


def label(rr, trail, hold, after=0.0):
    tp = 'TP1.5' if rr == 1.5 else 'noTP'
    tr = 'noTrail' if trail is None else f'trail{trail:g}'
    if trail is not None and after > 0:
        tr += f'@R{after:g}'
    h = 'noHold' if hold >= 100000 else f'h{hold}'
    return f'{tp}/{tr}/{h}'


def row(rr, trail, hold, after=0.0):
    full = m(run(BARS, rr, trail, hold, after))
    train = m(run(TRAIN, rr, trail, hold, after))
    test = m(run(TEST, rr, trail, hold, after))
    return dict(
        config=label(rr, trail, hold, after), rr=rr, trail=trail, hold=hold,
        trail_after_r=after,
        full_ret=full['ret'], full_dd=full['dd'], full_sharpe=full['sharpe'],
        full_trades=full['trades'], full_win=full['win'], full_avg=full['avg'],
        full_exposure=full['exposure'],
        train_ret=train['ret'], train_dd=train['dd'], train_sharpe=train['sharpe'],
        train_trades=train['trades'], train_win=train['win'],
        test_ret=test['ret'], test_dd=test['dd'], test_sharpe=test['sharpe'],
        test_trades=test['trades'], test_win=test['win'],
    )


rows = []
# Baselines: the shipped fixed 1.5R, and no target at all.
rows.append(row(1.5, None, 18))
rows.append(row(None, None, 18))

# No fixed target: let the trail own the exit.
for trail in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
    for hold in (18, 30, 60, 100000):
        rows.append(row(None, trail, hold))

# No fixed target, but do not arm the trail until the trade has paid.
for trail in (2.0, 2.5, 3.0, 3.5):
    for after in (1.0, 1.5, 2.0):
        for hold in (60, 100000):
            rows.append(row(None, trail, hold, after))

# Keep the 1.5R cap but add a trail: only tests whether the trail cuts losers.
for trail in (2.0, 2.5, 3.0):
    for hold in (18, 60):
        rows.append(row(1.5, trail, hold))
for trail in (2.5, 3.0):
    rows.append(row(1.5, trail, 60, 1.0))

out = ROOT / 'research' / 'uni' / 'uni_trail.csv'
with out.open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

print(f'wrote {out} ({len(rows)} configs)')
print(f"{'config':28} {'TRAIN ret':>9} {'dd':>7} {'sh':>5} {'n':>3} {'TEST ret':>9} {'dd':>7} {'sh':>5} {'n':>3}")
for r in rows:
    print(f"{r['config']:28} {r['train_ret']:9.2f} {r['train_dd']:7.2f} {r['train_sharpe']:5.2f} {r['train_trades']:3d} "
          f"{r['test_ret']:9.2f} {r['test_dd']:7.2f} {r['test_sharpe']:5.2f} {r['test_trades']:3d}")

# Trade shape and exit reasons for the promising no-target trail variants.
print('\n-- trade shape (full window) --')
for trail, hold, after in ((2.5, 100000, 1.5), (2.5, 100000, 2.0),
                          (3.0, 100000, 1.5), (3.0, 100000, 2.0),
                          (2.5, 100000, 0.0), (3.0, 100000, 0.0)):
    res = run(BARS, None, trail, hold, after)
    reasons = {}
    for t in res.trades:
        key = t.reason_close.split('(')[0].strip()
        reasons[key] = reasons.get(key, 0) + 1
    rets = [t.return_pct * 100 for t in res.trades]
    arm = f'@R{after:g}' if after else ''
    print(f"noTP/trail{trail:g}{arm}/h{hold if hold < 100000 else 'none'}: "
          f"n={len(rets)} avg={sum(rets)/len(rets):.2f}% best={max(rets):.2f}% "
          f"worst={min(rets):.2f}% exits={reasons}")

print('\n-- six-month folds (return / max DD / trades) --')
cases = (
    ('fixed 1.5R / 18', 1.5, None, 18, 0.0),
    ('no TP / 18', None, None, 18, 0.0),
    ('no TP / trail2.5 / none', None, 2.5, 100000, 0.0),
    ('no TP / trail3 / 18', None, 3.0, 18, 0.0),
    ('no TP / trail2.5 @1R / none', None, 2.5, 100000, 1.0),
)
for name, rr, trail, hold, after in cases:
    cells = []
    for k in range(4):
        a = START + pd.Timedelta(days=182 * k)
        b = START + pd.Timedelta(days=182 * (k + 1)) if k < 3 else BARS[-1].ts + pd.Timedelta(hours=4)
        x = m(run([bar for bar in BARS if a <= bar.ts < b], rr, trail, hold, after))
        cells.append(f"{x['ret']:+.1f}%/{x['dd']:.1f}%/{x['trades']}")
    print(f"{name:30} " + '  '.join(cells))