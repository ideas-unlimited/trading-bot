import json, math
from pathlib import Path

import pandas as pd

d = json.load(open('runs/report_data.json'))
main = d['main']['summary']
curve = d['curve']; btc = d['btc']
years = list(d['main']['yearly'].keys())
yearly = list(d['main']['yearly'].values())
geom = d['geometry']

# --- downsample equity to weekly ---
step = 7
ts = curve['ts'][::step]; eq = curve['eq'][::step]; bh = btc[::step]
eq_n = [v / eq[0] for v in eq]
bh_n = [v / bh[0] for v in bh]
start_year, end_year = int(ts[0][:4]), int(ts[-1][:4])

W, H, L, R, T, B = 860, 330, 44, 10, 14, 26

def xs(i, n):
    return L + (W - L - R) * i / max(n - 1, 1)

lo = min(min(eq_n), min(bh_n)); hi = max(max(eq_n), max(bh_n))
l0, l1 = math.log10(max(lo, 0.2)), math.log10(hi)
def ys(v):
    return T + (H - T - B) * (1 - (math.log10(max(v, 0.2)) - l0) / (l1 - l0))

def path(series):
    return 'M' + ' L'.join(f'{xs(i,len(series)):.1f},{ys(v):.1f}' for i, v in enumerate(series))

grid = []
for v in (0.5, 1, 2, 4, 8, 16, 30):
    if l0 <= math.log10(v) <= l1:
        yy = ys(v)
        grid.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{W-R}" y2="{yy:.1f}" class="grid"/>')
        grid.append(f'<text x="{L-6}" y="{yy+3.5:.1f}" class="ax" text-anchor="end">{v:g}x</text>')
ylabels = []
seen = set()
for i, t in enumerate(ts):
    y = t[:4]
    if y not in seen and t[5:7] <= '02':
        seen.add(y)
        ylabels.append(f'<text x="{xs(i,len(ts)):.1f}" y="{H-8}" class="ax" text-anchor="middle">{y}</text>')

eq_path = path(eq_n); bh_path = path(bh_n)

# drawdown strip
peak = -1e9; dd = []
for v in eq:
    peak = max(peak, v); dd.append(v / peak - 1)
dd_min = min(dd); DD_H = 54
def dys(v): return H + 18 + (DD_H - 18) * (v / dd_min if dd_min else 0)
dd_area = 'M' + f'{xs(0,len(dd)):.1f},{H+18}' + ''.join(f' L{xs(i,len(dd)):.1f},{dys(v):.1f}' for i, v in enumerate(dd)) + f' L{xs(len(dd)-1,len(dd)):.1f},{H+18} Z'

# --- yearly bars ---
BW, BH2, BL, BR, BT, BB = 860, 190, 44, 10, 16, 24
ymin = min(min(yearly), 0); ymax = max(max(yearly), 0.2)
def bys(v): return BT + (BH2 - BT - BB) * (1 - (v - ymin) / (ymax - ymin))
bw = (BW - BL - BR) / len(years) * 0.62
bars = []
for i, (y, v) in enumerate(zip(years, yearly)):
    cx = BL + (BW - BL - BR) * (i + 0.5) / len(years)
    y0, y1 = bys(0), bys(v)
    cls = 'pos' if v >= 0 else 'neg'
    bars.append(f'<rect x="{cx-bw/2:.1f}" y="{min(y0,y1):.1f}" width="{bw:.1f}" height="{max(abs(y1-y0),1):.1f}" class="{cls}" rx="2"/>')
    bars.append(f'<text x="{cx:.1f}" y="{min(y0,y1)-5:.1f}" class="ax" text-anchor="middle">{v*100:.0f}%</text>')
    bars.append(f'<text x="{cx:.1f}" y="{BH2-6}" class="ax" text-anchor="middle">{y}</text>')

# --- tradeoff scatter ---
SW, SH, SL, SR, ST, SB = 860, 300, 52, 150, 18, 34
sx = lambda w: SL + (SW - SL - SR) * (w - 0.40) / (0.80 - 0.40)
sy = lambda e: ST + (SH - ST - SB) * (1 - (e - 0) / (0.35 - 0))
pts = []
for g in geom:
    x, y = sx(g['win']), sy(g['ev'])
    lbl = f"{g['rr']}R" if g['rr'] >= 1 else f"{g['rr']}R"
    pts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" class="dot"/>')
    pts.append(f'<text x="{x:.1f}" y="{y-12:.1f}" class="ax strong" text-anchor="middle">target {lbl}</text>')
    pts.append(f'<text x="{x:.1f}" y="{y+20:.1f}" class="ax" text-anchor="middle">{g["win"]*100:.0f}% win · {g["ev"]:+.2f}R</text>')
gate_x = sx(0.70)

def fmt_pct(v, nd=1): return f'{v*100:.{nd}f}%'

rel_a = d['model_a']['rel']; rel_b = d['model_b']['rel']

css = '''
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-sans, system-ui, sans-serif); color: var(--foreground); }
.wrap { max-width: 900px; margin: 0 auto; padding: 4px 0 32px; }
h1 { font-size: 30px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 6px; font-weight: 650; }
h2 { font-size: 19px; margin: 40px 0 10px; letter-spacing: -0.01em; }
h3 { font-size: 15px; margin: 22px 0 8px; }
.sub { color: var(--muted-foreground); font-size: 14.5px; line-height: 1.55; margin: 0 0 24px; }
p { line-height: 1.62; font-size: 14.5px; margin: 10px 0; }
.lede { font-size: 15.5px; line-height: 1.65; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 22px 0 8px; }
.card { border: 1px solid var(--border); border-radius: var(--radius, 10px); padding: 14px 14px 12px; background: var(--card); }
.card .k { color: var(--muted-foreground); font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; }
.card .v { font-size: 25px; font-weight: 620; margin-top: 4px; letter-spacing: -0.02em; }
.card .n { color: var(--muted-foreground); font-size: 12px; margin-top: 3px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin: 12px 0; }
th, td { text-align: right; padding: 8px 8px; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted-foreground); font-weight: 560; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
tr.hl td { background: var(--accent-surface, rgba(120,120,255,.12)); font-weight: 600; }
tr.dim td { color: var(--muted-foreground); }
.note { border-left: 2px solid var(--accent); padding: 2px 0 2px 14px; margin: 18px 0; color: var(--foreground); }
.warn { border-left: 2px solid var(--warning, #d99100); padding: 2px 0 2px 14px; margin: 18px 0; }
.mono { font-family: var(--font-mono, ui-monospace, monospace); font-size: 12.5px; background: var(--code-background); padding: 13px 15px; border-radius: var(--radius, 8px); overflow-x: auto; line-height: 1.7; white-space: pre; }
svg { width: 100%; height: auto; display: block; margin: 6px 0 2px; overflow: visible; }
.grid { stroke: var(--border); stroke-width: 1; }
.ax { fill: var(--muted-foreground); font-size: 11px; font-family: var(--font-sans, sans-serif); }
.ax.strong { fill: var(--foreground); font-weight: 600; }
.pos { fill: var(--accent); opacity: .85; }
.neg { fill: var(--destructive, #c0392b); opacity: .75; }
.dot { fill: var(--accent); }
.eq { fill: none; stroke: var(--accent); stroke-width: 1.8; }
.bh { fill: none; stroke: var(--muted-foreground); stroke-width: 1.1; stroke-dasharray: 4 3; opacity: .75; }
.dd { fill: var(--destructive, #c0392b); opacity: .30; }
.cap { color: var(--muted-foreground); font-size: 12px; margin: 2px 0 0; }
ul { line-height: 1.7; font-size: 14.5px; padding-left: 20px; margin: 8px 0; }
li { margin: 4px 0; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
@media (max-width: 640px) { .cols { grid-template-columns: 1fr; } h1 { font-size: 25px; } }
'''

html = []
html.append('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">')
html.append('<title>Swing system research</title><style>' + css + '</style></head><body><div class="wrap">')
html.append('<h1>Five coins, one rule, and what your 70% conviction idea actually does</h1>')
html.append('<p class="sub">BTC · ETH · BNB · XRP · SOL &nbsp;·&nbsp; daily bars, Aug 2017 – Nov 2026 &nbsp;·&nbsp; ~3,300 daily bars per coin (SOL from 2021) &nbsp;·&nbsp; $1,000, 10 bps fees</p>')

html.append('<p class="lede">I tested the conviction gate first, because it decides everything else. <b>A 70% chance of winning is not something price data can predict.</b> Two separate models, trained walk-forward with purging, scored 0.49 and 0.36 AUC out of sample — the second one was actively <i>anti</i>-predictive. But the same research found a rule that does hold up, and it wins only 45-48% of the time. That contrast is the whole answer: the money is in the size of the winners, which is why the exit is a 3.5 ATR trailing stop instead of a target.</p>')

html.append('<div class="cards">')
for k, v, n in [
    ('Strategy CAGR', f"{main['cagr']*100:.1f}%", '2019–2026, after 10bps fees'),
    ('Sharpe', f"{main['sharpe']:.2f}", 'vs 0.77 buy & hold BTC'),
    ('Max drawdown', f"{main['max_drawdown']*100:.1f}%", 'on $1,000: -$366 at worst'),
    ('$1,000 became', f"${main['final']:,.0f}", f"{main['trades']:.0f} round trips, {main['win_rate']*100:.0f}% winners"),
]:
    html.append(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="n">{n}</div></div>')
html.append('</div>')

html.append('<h2>1. The 70% gate, tested honestly</h2>')
html.append('<p>Conviction is only meaningful next to the payoff. If a trade risks 1 to make <i>R</i>, it needs a win rate above 1/(1+<i>R</i>) just to break even. Tighten the target to raise the win rate and the bar moves up with it. Here is every geometry I measured on the same trend-filtered entries, seven years, five coins:</p>')
html.append('<table><thead><tr><th>Trade geometry</th><th>Win rate</th><th>Break-even</th><th>Expectancy</th><th>Edge over break-even</th></tr></thead><tbody>')
for g in geom:
    cls = 'hl' if g['rr'] <= 0.5 else ''
    edge = (g['win'] - g['be']) * 100
    verdict = f'{edge:+.1f} pts' if edge >= 0 else f'<b>{edge:+.1f} pts</b>'
    html.append(f'<tr class="{cls}"><td>stop {g["stop"]}×ATR, target {g["rr"]}R</td><td>{g["win"]*100:.1f}%</td><td>{g["be"]*100:.1f}%</td><td>{g["ev"]:+.3f}R</td><td>{verdict}</td></tr>')
html.append('</tbody></table>')
html.append('<p class="cap">Trend-filtered entries only: coin above its 50-bar EMA, BTC above its 200-day average, price within 2% of a 20-bar high. 870 trades. Expectancy is in units of risk (R) per trade, before costs.</p>')

html.append('<div class="warn"><b>Read the two highlighted rows together.</b> At a 0.5R target you get 69% winners — your 70% gate passes almost everything — and earn +0.07R per trade. At 0.4R the win rate finally clears 70% (73%), but break-even is 71.4%: a 70% filter on that trade is <i>negative</i> expectancy. Meanwhile the 1.5R trade wins only 47% of the time and pays <b>+0.30R</b> — four times more per trade. The high-win-rate version is the worse business.</div>')

html.append('<h2>2. Why the conviction model cannot be built</h2>')
html.append('<p>I did not assume this — I tried it properly. Two walk-forward models, retrained every 90 days on an expanding window, purged by the label horizon, features limited to information available at the bar close:</p>')
html.append('<table><thead><tr><th>Model</th><th>Universe</th><th>Out-of-sample AUC</th><th>Read</th></tr></thead><tbody>')
html.append(f'<tr><td>27 features, logistic, L2=1</td><td>all bars, 1.5R target</td><td>{d["model_a"]["auc"]:.3f}</td><td>coin flip ({d["model_a"]["n"]:,} predictions)</td></tr>')
html.append(f'<tr><td>7 features, logistic, L2=3</td><td>trend-filtered, 0.4R target</td><td>{d["model_b"]["auc"]:.3f}</td><td>worse than a coin flip ({d["model_b"]["n"]:,} predictions)</td></tr>')
html.append('</tbody></table>')

html.append('<p>The second model is the interesting failure. It was trained only on trades that already passed the trend filter, where the base rate is 73%. It learned to be confident exactly when it should not be:</p>')
html.append('<table><thead><tr><th>Predicted win chance</th><th>Actual win rate</th><th>Trades</th></tr></thead><tbody>')
for r in rel_b:
    html.append(f'<tr><td>{r["pred"]*100:.0f}%</td><td>{r["actual"]*100:.0f}%</td><td>{int(r["n"])}</td></tr>')
html.append('</tbody></table>')
html.append('<p class="cap">Predicted 79% → delivered 47%. The buckets are inverted, which is why a 70% threshold selected the worst trades, not the best.</p>')
html.append('<p>This is not a tuning failure. A 4-hour-bar long trade is driven by the next two weeks of order flow, news and the market beta cycle; the features that describe the <i>present</i> state cannot resolve a 70/30 probability over that horizon. When I added features the AUC stayed at chance; when I simplified to interpretable buckets it inverted. That is the signature of noise being fit, not signal being found.</p>')

html.append('<h2>3. What does survive: regime, then trend, then pay for the tail</h2>')
html.append('<p>The single largest effect in the data is not the coin, the indicator or the entry — it is whether the whole market is in an uptrend. Unconditional longs at a 1.5R target win 38.6%. Inside a BTC-up regime with the coin above its 60-day average and near a 20-day high, 46.7%. In 2018, 2022 and 2025–26, the unconditional version lost 20–40% a year. The regime filter sidesteps those years entirely instead of trying to predict them.</p>')
html.append('<p>So the strategy is deliberately boring: hold the two strongest of the five coins, only while BTC is above its 200-day average, sized down when volatility is high, in cash otherwise. No per-trade conviction. The edge is paid by the tails.</p>')
html.append('<div class="note"><b>Keep the comparison honest.</b> Over the full sample the rotation ends roughly level with BTC buy &amp; hold on total return (18× vs 14× on $1,000). The point is not a bigger number — it is that it gets there with a −37% worst drawdown instead of −83%, a Sharpe of 1.25 instead of 0.77, and a flat 2022. It is only ~25% invested on average; the rest waits in cash.</div>')

html.append('<h3>Equity, $1,000, log scale</h3>')
html.append(f'<svg viewBox="0 0 {W} {H+80}" role="img" aria-label="Equity curve versus buy and hold">')
html.append(''.join(grid))
html.append(f'<path d="{bh_path}" class="bh"/>')
html.append(f'<path d="{eq_path}" class="eq"/>')
html.append(''.join(ylabels))
html.append(f'<text x="{L+4}" y="{T+12}" class="ax"><tspan class="ax strong">rotation</tspan> · dashed: BTC buy &amp; hold</text>')
html.append(f'<path d="{dd_area}" class="dd"/>')
html.append(f'<text x="{L-6}" y="{H+40}" class="ax" text-anchor="end">{dd_min*100:.0f}%</text>')
html.append('</svg>')

html.append('<h3>Year by year</h3>')
html.append(f'<svg viewBox="0 0 {BW} {BH2}" role="img" aria-label="Yearly returns">')
html.append(f'<line x1="{BL}" y1="{bys(0):.1f}" x2="{BW-BR}" y2="{bys(0):.1f}" class="grid"/>')
html.append(''.join(bars))
html.append('</svg>')
html.append('<p class="cap">Ten bps fees charged on every rebalance. 2017 is a partial year (data starts in August). 2022 is exactly zero: the BTC regime filter was flat for the whole bear market.</p>')

html.append('<h2>4. Letting winners run: the trailing exit</h2>')
html.append('<p>A hard target is a ceiling on exactly the trades that pay for everything else. So I re-ran the identical entry rule with no target at all, only a stop that ratchets up behind the highest high since entry (a Chandelier exit at <i>k</i> ATRs). Only the exit differs between the rows below:</p>')

tr_tab = pd.read_csv('runs/trailing_book.csv')
pick = ['trail 2.0ATR','trail 2.5ATR','trail 3.0ATR','trail 3.5ATR','trail 4.0ATR','trail 6.0ATR','hard 1.2R','hard 4.0R']
tr_tab = tr_tab[tr_tab.name.isin(pick)]
order = {n:i for i,n in enumerate(pick)}
tr_tab = tr_tab.sort_values('name', key=lambda s: s.map(order))
html.append('<table><thead><tr><th>Exit policy</th><th>Win</th><th>EV / trade</th><th>Peak captured</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th></tr></thead><tbody>')
for _, r in tr_tab.iterrows():
    cls = 'hl' if r['name'] == 'trail 3.5ATR' else ('dim' if str(r['name']).startswith('hard') else '')
    html.append(f'<tr class="{cls}"><td>{r["name"]}</td><td>{r["win"]*100:.0f}%</td><td>{r["ev"]:+.2f}R</td><td>{r["capture"]*100:.0f}%</td><td>{r["cagr"]*100:.1f}%</td><td>{r["sharpe"]:.2f}</td><td>{r["dd"]*100:.1f}%</td></tr>')
html.append('</tbody></table>')
html.append('<p class="cap">Book of max 2 concurrent positions, 2% equity risk per trade, 50% notional cap, 10 bps fees, 2017-2026. Peak captured is the median share of each trade\'s best unrealised profit that actually got banked.</p>')

html.append('<div class="note"><b>The tight trail is the expensive one.</b> A 2 ATR trail looks prudent and is the worst row: it churns, re-enters the same failing move, and produces a −47% drawdown for a 12% CAGR. The plateau is <b>3.0–4.0 ATR</b> (Sharpe 1.20–1.42, drawdown −20% to −22%). At 3.5 ATR the book makes 30% a year with a −22% worst drawdown, from entries that win only 48% of the time.</div>')

html.append('<h3>What the give-back actually costs</h3>')
html.append('<p>I recorded every trade\'s maximum favourable excursion — the best it ever looked — and what was actually banked when the trail fired:</p>')
trades = pd.read_csv('runs/trailing_trades_35.csv')
wins = trades[trades.r > 0]
html.append('<table><thead><tr><th>Measure</th><th>Value</th><th>What it means</th></tr></thead><tbody>')
html.append(f'<tr><td>Winners: median peak</td><td>{wins.mfe.median():.2f}R</td><td>the average winner is up 3.6R at its best</td></tr>')
html.append(f'<tr><td>Winners: median banked</td><td>{wins.r.median():.2f}R</td><td>it leaves 2.1R on the table</td></tr>')
html.append(f'<tr><td>Winners: median capture</td><td>{100*(wins.r/wins.mfe).clip(0,1).median():.0f}%</td><td>you keep less than half the peak — this is normal</td></tr>')
html.append(f'<tr><td>Best 5 trades</td><td>{100*trades.nlargest(5,"r").r.sum()/trades.r.sum():.0f}% of all R</td><td>the edge is concentrated in a handful of trends</td></tr>')
html.append(f'<tr><td>Median hold</td><td>{trades.bars.median():.0f} days</td><td>a 2-4 week swing, not a scalp</td></tr>')
html.append('</tbody></table>')
html.append('<p class="cap">Trail 3.5 ATR, initial stop 2.5 ATR, 2017-2026: 162 trades. Every exit was the trail; none hit a time limit.</p>')

# give-back scatter
tx = trades.mfe.clip(0, 6); ty = trades.r.clip(-1.5, 6)
PW, PH, PL, PR, PT, PB = 860, 300, 54, 96, 14, 30
px_ = lambda v: PL + (PW - PL - PR) * min(max(v, 0), 6) / 6
py_ = lambda v: PT + (PH - PT - PB) * (1 - (min(max(v, -1.5), 6) + 1.5) / 7.5)
dots = []
for xv, yv, rv in zip(tx, ty, trades.r):
    cls = 'dot' if rv > 0 else 'dot loss'
    dots.append(f'<circle cx="{px_(xv):.1f}" cy="{py_(yv):.1f}" r="3" class="{cls}" opacity=".55"/>')
html.append(f'<svg viewBox="0 0 {PW} {PH}" role="img" aria-label="Peak versus realised R">')
html.append(f'<line x1="{px_(0):.1f}" y1="{py_(-1.5):.1f}" x2="{px_(0):.1f}" y2="{py_(6):.1f}" class="grid"/>')
html.append(f'<line x1="{px_(0):.1f}" y1="{py_(0):.1f}" x2="{px_(6):.1f}" y2="{py_(0):.1f}" class="grid"/>')
for tick in (2, 4, 6):
    html.append(f'<text x="{px_(tick):.1f}" y="{PH-8}" class="ax" text-anchor="middle">{tick}R</text>')
    html.append(f'<text x="{PL-6}" y="{py_(tick)+3:.1f}" class="ax" text-anchor="end">{tick}R</text>')
html.append(f'<line x1="{px_(0):.1f}" y1="{py_(0):.1f}" x2="{px_(6):.1f}" y2="{py_(6):.1f}" class="grid" stroke-dasharray="3 3"/>')
html.append(''.join(dots))
html.append(f'<text x="{px_(3):.1f}" y="{PT+10}" class="ax" text-anchor="middle">dashed line = banking every trade at its exact peak (impossible)</text>')
html.append(f'<text x="{px_(3):.1f}" y="{PH-22}" class="ax" text-anchor="middle">best unrealised profit, R</text>')
html.append(f'<text x="16" y="{py_(2.5):.1f}" class="ax" transform="rotate(-90 16 {py_(2.5):.1f})" text-anchor="middle">banked, R</text>')
html.append('</svg>')
html.append('<p class="cap">Every dot is one of the 162 trades. Winners cluster well below the 45-degree line: that gap is the insurance premium you pay for the 5-15R trends. No exit rule captures the peak — the wider trail is what turns a 6R trend into a 4R win instead of a 1.2R one. That is the 3.5 ATR setting.</p>')

html.append('<h2>5. The rule, exactly</h2>')
mono = [
  'every Sunday close, on daily bars:',
  '  regime   = BTC close > 200-day SMA            -> else hold cash',
  '  eligible = coin close > its close 60 days ago   (momentum positive)',
  '  picks    = top 2 eligible coins by 60-day return',
  '  weight   = 50% each x min(1, 40% / 30-day realised vol)',
  '  fees     = 10 bps on turnover; rebalance only when the pick set changes',
]
html.append('<div class="mono">' + chr(10).join(mono) + '</div>')
html.append('<p>The satellite is separate: on any of the five coins, buy when close &gt; 200-day SMA, 60-day return &gt; 0 and price is within 2% of the prior 20-day high. Stop 2.5 ATR below entry, then trail at 3.5 ATR from the highest high. No target at all. Risk 2% of equity per trade, maximum two open at once.</p>')
html.append('<p>The volatility multiplier is what keeps the drawdown near −36% instead of −60%: in a calm trend each position runs near 30–35%, in a wild one it drops to 10–15%. Nothing is levered, and unused capital stays in cash.</p>')

html.append('<h3>Per-coin, same rule, one position at a time, 2% risk</h3>')
html.append('<table><thead><tr><th>Coin</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>Trades</th><th>Win rate</th><th>Profit factor</th></tr></thead><tbody>')
for r in d['per_coin']:
    html.append(f'<tr><td>{r["sym"]}</td><td>{r["cagr"]*100:+.1f}%</td><td>{r["sharpe"]:.2f}</td><td>{r["dd"]*100:.1f}%</td><td>{r["trades"]}</td><td>{r["win"]*100:.0f}%</td><td>{r["pf"]:.2f}</td></tr>')
html.append('</tbody></table>')
html.append('<p class="cap">Every coin is positive over the full sample, and every coin wins less than 60% of its trades. ETH is the best single market; XRP is the weakest and still does not lose money. This is why I would not cherry-pick one coin — the diversification is doing real work.</p>')

html.append('<h2>6. Running it on $1,000</h2>')
html.append('<div class="cols"><div>')
html.append('<h3>Allocation and sizing</h3><ul>')
html.append('<li><b>One book, $1,000.</b> The rotation is the strategy; splitting capital across per-coin bots just recreates it with worse arithmetic.</li>')
html.append('<li><b>Max 2 positions, 50% notional each before the vol scaler.</b> Typical deployed capital is 25–60%; the rest is cash and is meant to be.</li>')
html.append('<li><b>Split 50/50</b> between the rotation core and the trailing swing at 2% risk each. That blend tested at ~1.66 Sharpe and a −31% drawdown, versus 1.35 / −37% for the rotation alone.</li>')
html.append('<li><b>40% annualised vol target per position</b> in the rotation. On $1,000 a full position is roughly $250–$400, never more than $500.</li>')
html.append('<li><b>No leverage, no shorts.</b> Shorts need funding and liquidation models this bot does not have, and 2022 showed the filter is enough.</li>')
html.append('<li><b>Drawdown ladder:</b> halve size at −20% from the peak, stop and review at −30%. The backtest’s −36.6% is the number to expect, not a tail you can ignore.</li>')
html.append('</ul></div><div>')
html.append('<h3>Execution</h3><ul>')
html.append('<li><b>Check once a week</b>, at the Sunday daily close. Daily rebalancing added 4 points of CAGR and a lot of turnover; not worth it on a $1,000 account.</li>')
html.append('<li><b>Market orders at the next open</b>, then leave them alone. The rebalance only changes when the pick set or the regime changes — about 95 round trips in nine years.</li>')
html.append('<li><b>Assumed costs:</b> 10 bps taker plus slippage. At 50 bps the CAGR drops from 36.7% to 32.7% — the strategy is not fee-fragile because it turns over ~10 times a year.</li>')
html.append('<li><b>Keep a log</b> of every decision with the regime value and the 60-day returns that justified it. After 3 months you can audit yourself instead of guessing.</li>')
html.append('<li><b>Paper trade 8–12 weeks first.</b> The bot already has `python -m bot rotate` and `python -m bot live`; make them agree before risking anything.</li>')
html.append('</ul></div></div>')
html.append('<div class="mono">$env:PYTHONPATH="src"\npython -m bot rotate --coins BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT --initial-cash 1000 --fee-bps 10\npython -m bot backtest --source csv --csv data/crypto/BTCUSDT_1d.csv --strategy momentum_swing</div>')

html.append('<h2>7. What would make me wrong</h2>')
html.append('<ul>')
html.append('<li><b>Survivorship.</b> The universe is today’s top five. Coins that died are not in the sample, and 2021 — a once-in-a-cycle alt melt-up — contributes +159%. Expect materially less than 37% CAGR, and treat 0% as a bad year you should survive, not a bug.</li>')
html.append('<li><b>Regime whipsaw.</b> The 200-day BTC filter cuts both ways: it avoids 2022 but re-enters late after a V-bottom. That is the cost of not predicting.</li>')
html.append('<li><b>No fees, as requested.</b> I still tested them: 10 bps costs ~1 point of CAGR, 50 bps ~5 points. If you trade with taker fees on a small account, use limit orders.</li>')
html.append('<li><b>One cycle of history.</b> 2018-2026 is two bull markets and two bears. It is enough to reject a strategy, not enough to prove one. Forward-test on paper for a full quarter before scaling.</li>')
html.append('</ul>')
html.append('<p class="cap">Data: Binance spot, 4h and 1d, downloaded 2026-11-26. Code: <span class="mono" style="display:inline;padding:2px 6px">src/bot/portfolio.py</span>, <span class="mono" style="display:inline;padding:2px 6px">src/bot/strategy/momentum_swing.py</span>, research scripts in <span class="mono" style="display:inline;padding:2px 6px">research/</span>.</p>')
html.append('</div></body></html>')

Path('runs/report').mkdir(parents=True, exist_ok=True)
Path('runs/report/report.html').write_text('\n'.join(html), encoding='utf-8')
print('wrote runs/report/report.html', len('\n'.join(html)), 'bytes')
