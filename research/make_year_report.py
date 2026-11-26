import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs' / 'report' / 'year.html'
COINS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'SOLUSDT']
NL = chr(10)

CSS = '''
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; background: transparent; color: var(--foreground, #16181d);
       font-family: var(--font-sans, ui-sans-serif, system-ui, -apple-system, sans-serif);
       font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased; }
.wrap { max-width: 760px; margin: 0 auto; padding: 6px 0 48px; }
.kicker { font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--muted-foreground, #6b7280); }
h1 { font-size: 34px; line-height: 1.08; letter-spacing: -.022em; margin: 10px 0 14px; font-weight: 650; }
.dek { font-size: 17px; color: var(--muted-foreground, #4b5563); margin: 0 0 26px; max-width: 60ch; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border, #e5e7eb);
         border: 1px solid var(--border, #e5e7eb); border-radius: var(--radius, 10px); overflow: hidden; margin: 0 0 30px; }
.stat { background: var(--background, #fff); padding: 13px 14px 11px; }
.stat b { display: block; font-size: 23px; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
.stat span { font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted-foreground, #6b7280); }
.up { color: var(--success, #15803d); } .down { color: var(--destructive, #b91c1c); }
h2 { font-size: 12.5px; letter-spacing: .13em; text-transform: uppercase; color: var(--muted-foreground, #6b7280);
     margin: 42px 0 12px; padding-top: 14px; border-top: 1px solid var(--border, #e5e7eb); display: flex; gap: 10px; font-weight: 600; }
h2 em { font-style: normal; color: var(--accent, #2563eb); }
h3 { font-size: 16.5px; margin: 22px 0 6px; }
p { margin: 0 0 12px; }
.lede { font-size: 16px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 14px; margin: 12px 0 18px; }
th { text-align: left; font-size: 10.5px; letter-spacing: .07em; text-transform: uppercase; color: var(--muted-foreground, #6b7280);
     font-weight: 600; padding: 7px 8px; border-bottom: 1px solid var(--border, #e5e7eb); }
td { padding: 7px 8px; border-bottom: 1px solid var(--border, #e5e7eb); }
td.n, th.n { text-align: right; }
tr.hi td { background: var(--accent-surface, rgba(37, 99, 235, .10)); }
.note { border-left: 3px solid var(--accent, #2563eb); padding: 1px 0 1px 14px; color: var(--muted-foreground, #4b5563); margin: 18px 0 22px; font-size: 14px; }
.caveat { background: var(--warning-surface, rgba(245, 158, 11, .10)); border-radius: var(--radius, 10px);
          padding: 13px 16px; font-size: 14px; margin: 18px 0; }
.rule { list-style: none; padding: 0; margin: 8px 0 18px; counter-reset: r; }
.rule li { position: relative; padding: 9px 0 9px 36px; border-bottom: 1px dashed var(--border, #e5e7eb); }
.rule li:last-child { border-bottom: 0; }
.rule li:before { counter-increment: r; content: counter(r); position: absolute; left: 0; top: 10px; width: 22px; height: 22px;
                  border-radius: 50%; background: var(--accent, #2563eb); color: var(--accent-foreground, #fff);
                  font-size: 12px; display: grid; place-items: center; font-weight: 600; }
.fig { margin: 14px 0 6px; }
.cap { font-size: 12px; color: var(--muted-foreground, #6b7280); margin: 4px 0 0; }
pre { background: var(--code-background, rgba(127,127,127,.09)); color: var(--code-foreground, inherit);
      padding: 12px 14px; border-radius: var(--radius, 10px); overflow-x: auto; font-size: 12.5px; line-height: 1.55;
      white-space: pre-wrap; word-break: break-word;
      font-family: var(--font-mono, ui-monospace, SFMono-Regular, monospace); }
.legend { display: flex; gap: 18px; font-size: 12px; color: var(--muted-foreground, #6b7280); margin-top: 2px; }
.sw { display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 7px; border-radius: 2px; }
.heat { border-collapse: separate; border-spacing: 3px; margin: 10px 0 6px; }
.heat td { text-align: center; padding: 7px 2px; font-size: 12.5px; border: 0; border-radius: 4px; }
.heat th { text-align: center; border: 0; padding: 4px; }
.heat td.best { outline: 2px solid var(--foreground, #111); outline-offset: -2px; font-weight: 700; }
.heat td.rowh { text-align: right; color: var(--muted-foreground, #6b7280); font-size: 12px; padding-right: 8px; }
.mono { font-family: var(--font-mono, ui-monospace, monospace); font-size: 13px; }
.foot { margin-top: 44px; padding-top: 14px; border-top: 1px solid var(--border, #e5e7eb); font-size: 12px;
        color: var(--muted-foreground, #6b7280); }
@media (max-width: 560px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  h1 { font-size: 28px; }
  .dek { font-size: 16px; }
}
'''


def pct(x, d=1):
    return f'{x * 100:+.{d}f}%'


def read_equity(name):
    s = pd.read_csv(ROOT / 'runs' / name / 'equity.csv', index_col=0, parse_dates=True).iloc[:, 0]
    return s.astype(float)


def bh_path(start):
    series = {}
    for c in COINS:
        d = pd.read_csv(ROOT / 'data' / 'crypto' / f'{c}_4h.csv')
        d['ts'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
        s = d.set_index('ts')['close']
        series[c] = s[~s.index.duplicated(keep='last')]
    df = pd.DataFrame(series).sort_index()
    df = df[df.index >= start].ffill().dropna()
    rel = df / df.iloc[0]
    return rel.mean(axis=1) - 1.0


def _nice_step(raw):
    if raw <= 0:
        return 1.0
    exp = np.floor(np.log10(raw))
    f = raw / 10 ** exp
    if f <= 1:
        nf = 1.0
    elif f <= 2:
        nf = 2.0
    elif f <= 2.5:
        nf = 2.5
    elif f <= 5:
        nf = 5.0
    else:
        nf = 10.0
    return nf * 10 ** exp


def line_chart(series, colors, names, height=300, percent=True):
    idx = series[0].index
    n = len(idx)
    vals = [s.to_numpy(float) for s in series]
    lo = min(float(v.min()) for v in vals)
    hi = max(float(v.max()) for v in vals)
    span = (hi - lo) or (0.02 if percent else 1.0)
    lo -= span * 0.06
    hi += span * 0.06
    tstep = _nice_step((hi - lo) / 6)
    W, H = 720, height
    L, R, T, B = 56, 66, 14, 30

    def X(i):
        return L + (i / max(n - 1, 1)) * (W - L - R)

    def Y(v):
        return T + (hi - v) / (hi - lo) * (H - T - B)

    def fmt(v):
        if percent:
            if abs(v) < tstep * 1e-6:
                return '0%'
            d = 0 if tstep >= 0.095 else 1
            return f'{v * 100:+.{d}f}%'
        return f'{v:,.0f}'

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" style="display:block">']
    ticks = np.arange(np.ceil(lo / tstep) * tstep, hi, tstep)
    if len(ticks) < 3:
        ticks = np.linspace(lo, hi, 5)
    for tv in ticks:
        y = Y(tv)
        zero = abs(tv) < tstep * 1e-6
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" '
                   f'stroke="var(--border, #e5e7eb)" stroke-width="{1.2 if zero else 0.8}" '
                   f'stroke-dasharray="{"" if zero else "2 4"}"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10.5" '
                   f'fill="var(--muted-foreground, #6b7280)">{fmt(tv)}</text>')
    for k in range(4):
        i = round(k * (n - 1) / 3)
        out.append(f'<text x="{X(i):.1f}" y="{H - 8}" text-anchor="{"start" if k == 0 else ("end" if k == 3 else "middle")}" '
                   f'font-size="10.5" fill="var(--muted-foreground, #6b7280)">{idx[i]:%b %y}</text>')
    for s, color in zip(series, colors):
        v = s.to_numpy(float)
        step = max(n // 360, 1)
        pts = [(X(i), Y(v[i])) for i in range(0, n, step)]
        if pts[-1][0] < X(n - 1):
            pts.append((X(n - 1), Y(v[-1])))
        d = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts)
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.1" stroke-linejoin="round" stroke-linecap="round"/>')
        out.append(f'<text x="{X(n - 1) + 8:.1f}" y="{Y(v[-1]) + 4:.1f}" font-size="11.5" font-weight="600" fill="{color}">{fmt(v[-1])}</text>')
    out.append('</svg>')
    legend = ''.join(f'<span><i class="sw" style="background:{c}"></i>{nm}</span>' for c, nm in zip(colors, names))
    return NL.join(out) + f'<div class="legend">{legend}</div>'


def bar_chart(labels, values, height=190):
    W, H = 720, height
    L, R, T, B = 52, 14, 14, 30
    lo = min(min(values), 0)
    hi = max(max(values), 0)
    span = (hi - lo) or 1
    lo -= span * 0.08
    hi += span * 0.08
    span = hi - lo

    def Y(v):
        return T + (hi - v) / span * (H - T - B)

    n = len(values)
    slot = (W - L - R) / n
    bw = slot * 0.52
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" style="display:block">']
    out.append(f'<line x1="{L}" y1="{Y(0):.1f}" x2="{W - R}" y2="{Y(0):.1f}" stroke="var(--border, #e5e7eb)" stroke-width="1.2"/>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = L + slot * i + (slot - bw) / 2
        y0, y1 = Y(0), Y(v)
        top, hgt = min(y0, y1), abs(y1 - y0)
        color = 'var(--success, #15803d)' if v >= 0 else 'var(--destructive, #b91c1c)'
        out.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(hgt, 1):.1f}" rx="2" fill="{color}" opacity="0.82"/>')
        ty = top - 5 if v >= 0 else top + hgt + 13
        out.append(f'<text x="{x + bw / 2:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="10.5" font-weight="600" fill="{color}">{v * 100:+.0f}%</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{H - 8}" text-anchor="middle" font-size="10" fill="var(--muted-foreground, #6b7280)">{lab}</text>')
    out.append('</svg>')
    return NL.join(out)


def heat_cell(v):
    a = min(abs(v) / 38.0, 1.0) * 0.62
    base = '34, 197, 94' if v >= 0 else '239, 68, 68'
    return f'background: rgba({base}, {a:.2f});'


book1 = read_equity('book1y')
book3 = read_equity('book3y')
bench = bh_path(pd.Timestamp('2025-11-26', tz='UTC'))
bench = bench.reindex(book1.index).ffill().bfill()

chart1 = line_chart([book1 / book1.iloc[0] - 1.0, bench],
                    ['var(--accent, #2563eb)', 'var(--muted-foreground, #9ca3af)'],
                    ['The book', 'Five coins, buy & hold'])

chart3 = line_chart([book3], ['var(--accent, #2563eb)'], ['The book'], percent=False, height=280)

monthly = book1.resample('ME').last().pct_change().dropna()
mlabels = [f'{i:%b}' for i in monthly.index]
mvalues = [float(v) for v in monthly.values]
chart_m = bar_chart(mlabels, mvalues)

years = [('Nov 23 - Nov 24', 1.171), ('Nov 24 - Nov 25', 0.415), ('Nov 25 - Nov 26', 0.376)]
chart_y = bar_chart([y[0] for y in years], [y[1] for y in years], height=170)

heat_fast = [26, 28, 30, 32, 34]
heat_slow = [44, 46, 48, 50, 52, 54, 56]
heat = [
    [-12.3, -17.7, -8.2, -1.9, -12.7, -1.7, -6.5],
    [-0.4, -2.5, 10.3, 2.6, 4.6, 18.3, -5.9],
    [4.7, 3.6, 15.7, 37.6, 27.1, 10.5, 16.6],
    [-1.4, -19.2, 3.1, 5.4, 11.9, 13.5, 0.4],
    [2.4, -12.4, -8.1, 7.6, 0.7, 5.4, 4.6],
]
heat_rows = []
for f, row in zip(heat_fast, heat):
    cells = []
    for s, v in zip(heat_slow, row):
        cls = ' class="best"' if (f == 30 and s == 50) else ''
        cells.append(f'<td{cls} style="{heat_cell(v)}">{v:+.0f}</td>')
    heat_rows.append(f'<tr><td class="rowh">{f}</td>{"".join(cells)}</tr>')
heat_html = (f'<table class="heat"><tr><th></th>' + ''.join(f'<th>{s}</th>' for s in heat_slow) + '</tr>'
             + ''.join(heat_rows) + '</table>')

bh = [('BTC', -32.9, -39.5), ('ETH', -43.8, -53.3), ('BNB', -38.2, -43.0),
      ('SOL', -50.5, -57.6), ('XRP', -57.2, -61.2)]
bh_rows = ''.join(
    f'<tr><td>{n}</td><td class="n down">{r:+.1f}%</td><td class="n">{d:+.1f}%</td></tr>'
    for n, r, d in bh
)

fam = [('Cross', '+11.6%', '+0.6%', '168'), ('Momentum', '+5.9%', '-4.8%', '252'),
       ('Dip / RSI', '+4.4%', '-3.7%', '210'), ('Breakout', '+1.5%', '-6.4%', '140')]
fam_rows = ''.join(
    f'<tr><td>{a}</td><td class="n up">{b}</td><td class="n">{c}</td><td class="n">{d}</td></tr>'
    for a, b, c, d in fam
)

h1h = [('60 / 120', -42.8, -47.5, 245), ('90 / 180', -21.4, -37.2, 171), ('120 / 240', 6.5, -21.4, 116),
       ('180 / 300', -31.8, -36.0, 109), ('120 / 300', -18.0, -31.4, 109), ('180 / 360', -29.9, -35.1, 98),
       ('240 / 480', -22.1, -25.8, 80), ('90 / 300', -9.1, -22.2, 118)]
h1h_rows = ''.join(
    f'<tr><td class="mono">{a}</td><td class="n {"up" if b > 0 else "down"}">{b:+.1f}%</td>'
    f'<td class="n">{d:.1f}%</td><td class="n">{c}</td></tr>'
    for a, b, d, c in h1h
)

body = rf'''
<div class="wrap">
  <div class="kicker">Last 12 months &middot; five coins &middot; one wallet &middot; 26 Nov 2026</div>
  <h1>The best rule I could find in the last year</h1>
  <p class="dek">I tested 1,750 variants on the last twelve months, then re-checked every survivor against the
  three-year data cap. The winner is a 4h moving-average cross that risks 2% a trade and sits in cash
  almost half the time. It made <b>40.6%</b> while the five coins fell 42.6%.</p>

  <div class="stats">
    <div class="stat"><b class="up">+40.6%</b><span>Last 12 months</span></div>
    <div class="stat"><b>-12.4%</b><span>Max drawdown</span></div>
    <div class="stat"><b>1.50</b><span>Sharpe</span></div>
    <div class="stat"><b>32</b><span>Round trips</span></div>
  </div>

  <h2><em>01</em> The year it had to beat</h2>
  <p class="lede">Every coin in the book fell. A long-only system starts this race roughly forty points behind,
  so the honest benchmark is not zero &mdash; it is minus forty-two.</p>
  <table>
    <tr><th>Coin</th><th class="n">Return</th><th class="n">Max drawdown</th></tr>
    {bh_rows}
    <tr class="hi"><td><b>Equal weight</b></td><td class="n down"><b>-42.6%</b></td><td class="n"><b>-49.2%</b></td></tr>
  </table>
  <p>A rule that simply holds cash returns 0% and beats all of them. Anything above zero is the edge; the
  40.6% is what the edge was worth when it was allowed to compound.</p>

  <h2><em>02</em> The search</h2>
  <p>Four families (moving-average cross, momentum, breakout, RSI dip), seven exit styles, five stop widths,
  two timeframes, five coins, one shared wallet. Each variant is a complete strategy run bar by bar &mdash;
  signal at the close, fill at the next open, 10 bps fees, 5 bps slippage.</p>
  <p><span class="mono">1,750</span> configs &rarr; <span class="mono">1,519</span> with at least six trades &rarr;
  <span class="mono">12</span> shortlisted &rarr; <span class="mono">1</span> shipped.</p>
  <table>
    <tr><th>Family</th><th class="n">Best config</th><th class="n">Median config</th><th class="n">N</th></tr>
    {fam_rows}
  </table>
  <p>The typical rule lost money. The median 4h config returned -3.7%; only 204 of 770 made anything at all.
  That is the base rate against which the winner has to be judged &mdash; not against zero.</p>

  <h2><em>03</em> The winner</h2>
  <p class="lede">SMA 30/50 on 4h bars. Enter when the fast average crosses the slow one, stop 2 ATRs below
  the entry, exit when the averages cross back. One position at a time, 2% of equity at risk, tie-break by
  30-bar momentum when two coins fire together.</p>
  <ol class="rule">
    <li><b>Entry</b> &mdash; SMA(30) crosses above SMA(50) on a completed 4h bar; buy the next open.</li>
    <li><b>Stop</b> &mdash; 2 &times; ATR(14) below the entry, sized so a stop-out costs 2% of equity.</li>
    <li><b>Exit</b> &mdash; SMA(30) crosses back below SMA(50); no target, no trailing stop.</li>
    <li><b>Slot</b> &mdash; one coin at a time; the strongest 30-bar return wins when several fire.</li>
  </ol>
  <div class="fig">{chart1}</div>
  <p class="cap">The book against an equal-weight basket of the same five coins, 26 Nov 2025 to 26 Nov 2026.
  The strategy was in the market 51% of the time.</p>
  <div class="grid2">
    <div>
      <div class="fig">{chart_m}</div>
      <p class="cap">Monthly returns. August (+31.2%) carried the year; June, October and November gave some back.</p>
    </div>
    <div>
      <h3>What the numbers say</h3>
      <p>32 round trips, 16 winners and 16 losers, average <b>+0.84R</b>. The win rate is deliberately low:
      the system pays for many small stops with a few large trends. Fees and slippage cost 54 &mdash;
      about an eighth of the gross gain.</p>
      <p>Max drawdown -12.4%, Calmar 3.26. Exposure 51% &mdash; half the year in cash is the feature, not a bug.</p>
    </div>
  </div>

  <h2><em>04</em> Three years, same wallet</h2>
  <p>The same rule run from 26 Nov 2023, the oldest date the cap allows.</p>
  <div class="stats">
    <div class="stat"><b class="up">+322.6%</b><span>Total return</span></div>
    <div class="stat"><b>61.6%</b><span>CAGR</span></div>
    <div class="stat"><b>-24.8%</b><span>Max drawdown</span></div>
    <div class="stat"><b>1.61</b><span>Sharpe</span></div>
  </div>
  <div class="fig">{chart3}</div>
  <p class="cap">$1,000 to $4,226 over three years, 104 round trips, 35.6% win rate, +1.19R average.</p>
  <div class="fig">{chart_y}</div>
  <p class="cap">Three consecutive 12-month blocks. The first contains the once-in-a-cycle XRP month;
  the last two do not.</p>

  <h2><em>05</em> What did not work</h2>
  <p><b>Faster bars.</b> The same family on 1h data is a disaster: 245 trades, -42.8% for the year, and even
  the best hourly pair only reached +6.5%. Fees and whipsaw eat the edge.</p>
  <table>
    <tr><th>1h pair</th><th class="n">Last year</th><th class="n">Max DD</th><th class="n">Trades</th></tr>
    {h1h_rows}
  </table>
  <p><b>Fixed targets.</b> Capping a trade at 2R is not crazy &mdash; the median fixed-target config actually
  beat the median cross exit (+1.9% against -0.3%). But the best fixed-target config made +8.7% against
  +11.6% for the pure cross exit: a target helps the average rule and truncates the exceptional one.
  Since the whole point is the tail, I kept the exit that gives it room.</p>
  <p><b>Mean reversion.</b> The best dip-buying config made +4.4% for the year, with a median of -3.7%.
  Buying weakness in a downtrend is how you end up holding the whole decline.</p>

  <h2><em>06</em> Where it breaks</h2>
  <p>This is the part I would want to know if it were my money.</p>
  <div class="caveat">
    <b>The result is a plateau, not a spike &mdash; but 30/50 sits on the high point of it.</b>
    Its four immediate neighbours made +2.6%, +5.4%, +15.7% and +27.1% for the year, and most of this grid
    is positive over three years (mostly +100% to +300%). The family is real; the exact +40.6% is inflated
    by selection.
  </div>
  {heat_html}
  <p class="cap">Last-year return by fast/slow pair (4h, 2 ATR stop, one slot). The boxed cell is the shipped rule.</p>
  <div class="caveat">
    <b>One trade paid for the year.</b> The best ETH trade (+29.5%, August 2026) was 82% of the profit.
    Remove it and the year is still positive, at +7.4%. Over three years the XRP trade of Nov 2024
    (+333%) was 60% of the profit; remove it and the three years still return +130%.
    A trend system without a fat winner is just a slow bleed &mdash; the tail is the product.
  </div>
  <p><b>What I would not conclude:</b> that this repeats. The honest expectation is a long-run CAGR in the
  low-to-mid teens with -25% drawdowns, and the occasional +100% year when a coin goes vertical.
  The 61.6% three-year CAGR is a good outcome, not a forecast.</p>

  <h2><em>07</em> Run it</h2>
  <p>The rule is a config of the existing engine, plus a new <span class="mono">book</span> command that
  shares one wallet across the coins.</p>
  <pre>$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m bot book --timeframe 4h --fast 30 --slow 50 --atr-mult 2.0 --risk-per-trade 0.02 --initial-cash 1000 --fee-bps 10 --outdir runs/book
</pre>
  <p>Drop <span class="mono">--start</span> to replay everything, or pass
  <span class="mono">--start 2025-11-26</span> to see the year on its own. The command prints the equity
  curve, the trade ledger and what the rule says for the next open on each coin.</p>
  <p>To trade it by hand: one position at a time, size so the 2 ATR stop costs 2% of the account, and take
  the next open after a completed 4h close. To run it as five separate $200 bots instead, expect about a
  fifth of the return &mdash; the cap, not the signal, is what binds.</p>

  <div class="foot">
    All figures measured on Binance 4h bars from 26 Nov 2023 to 26 Nov 2026, the three-year cap. Signals use
    completed bars only and fill at the next open; 10 bps fee and 5 bps slippage on both sides; 2% risk per
    trade with a 95% position cap; no leverage, no shorting. Buy &amp; hold is the equal-weight basket of the
    same five coins over the same window. Past performance is not a forecast, and a rule chosen on one year
    is a hypothesis, not a law.
  </div>
</div>
'''

html = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>The best rule in the last year</title><style>{CSS}</style></head><body>{body}</body></html>'
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html, encoding='utf-8')
print('wrote', OUT, len(html), 'bytes')
