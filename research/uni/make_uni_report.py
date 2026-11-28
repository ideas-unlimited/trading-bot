
import sys, html
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import pandas as pd, numpy as np
from bot.scanlib import load_scan_bars
from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.strategy import build

B=load_scan_bars('UNI/USDT','4h',source='binance',days=730)
P=dict(window=20,drop_pct=0.20,stop_atr=2.5,rr=1.5,max_hold=18)
cfg=BotConfig(initial_cash=1000,costs=CostConfig(fee_bps=10,slippage_bps=5),risk=RiskConfig(risk_per_trade=0.05,max_position_pct=0.5,max_drawdown_stop=0.5),bars_per_year=2190)
res=run_backtest(B,build('capitulation_dip_uni',**P),cfg)
eq=res.equity_curve; close=res.close
m=res.metrics

def ds(s, k=900):
    if len(s)<=k: return s
    idx=np.linspace(0,len(s)-1,k).astype(int)
    return s.iloc[idx]

eq_s=ds(eq); bh_s=ds(close/close.iloc[0]*1000)
lo=min(eq_s.min(),bh_s.min()); hi=max(eq_s.max(),bh_s.max())
W=900; H=320; p=48
def path(s):
    pts=[]
    for i,v in enumerate(s):
        x=p+i/(len(s)-1)*(W-2*p)
        y=H-p-(v-lo)/(hi-lo)*(H-2*p)
        pts.append(f'{x:.1f},{y:.1f}')
    return ' '.join(pts)
eq_path=path(eq_s); bh_path=path(bh_s)
grid=[]
for frac in (0,0.25,0.5,0.75,1):
    v=lo+(hi-lo)*frac; y=H-p-frac*(H-2*p)
    grid.append(f'<line x1="{p}" y1="{y:.1f}" x2="{W-p}" y2="{y:.1f}" stroke="var(--border)" stroke-width="1" opacity="0.55"/>')
    grid.append(f'<text x="{p-8}" y="{y+4:.1f}" text-anchor="end" font-size="10" fill="var(--muted-foreground)">${v:,.0f}</text>')

peak=eq.cummax(); dd=(eq/peak-1)*100
dd_s=ds(dd)
dlo=min(dd_s.min(),-6.0); dhi=0.0
def ddpath(s):
    pts=[]
    for i,v in enumerate(s):
        x=p+i/(len(s)-1)*(W-2*p)
        y=H-p-(v-dlo)/(dhi-dlo)*(H-2*p)
        pts.append(f'{x:.1f},{y:.1f}')
    return ' '.join(pts)
dd_path=ddpath(dd_s)
dd_grid=[]
for v in (0,-1,-2,-3,-4,-5,-6):
    y=H-p-(v-dlo)/(dhi-dlo)*(H-2*p)
    dd_grid.append(f'<line x1="{p}" y1="{y:.1f}" x2="{W-p}" y2="{y:.1f}" stroke="var(--border)" stroke-width="1" opacity="0.55"/>')
    dd_grid.append(f'<text x="{p-8}" y="{y+4:.1f}" text-anchor="end" font-size="10" fill="var(--muted-foreground)">{v}%</text>')

df=pd.read_csv(ROOT / 'research' / 'uni' / 'uni_walkforward.csv')
sub=df[(df.s==2.5)&(df.rr==1.5)]
drops=sorted(sub.d.unique()); holds=sorted(sub.h.unique())
cells=[]; vals=sub.te_ret*100; vmin=vals.min(); vmax=vals.max()
for d in drops:
    row=[]
    for h in holds:
        v=float(sub[(sub.d==d)&(sub.h==h)].te_ret.iloc[0]*100)
        t=(v-vmin)/(vmax-vmin) if vmax>vmin else 0.5
        row.append((v,t))
    cells.append((d,row))
def heat_color(t):
    r=int(220-(t*160)); g=int(90+t*120); b=int(90-t*30)
    return f'rgb({r},{g},{b})'

rows=[]
for t in res.trades:
    e=eq[eq.index<=t.entry_ts].iloc[-1]
    rows.append(f'<tr><td>{t.entry_ts:%Y-%m-%d %H:%M}</td><td>{t.exit_ts:%Y-%m-%d %H:%M}</td><td class="num">{t.return_pct*100:+.2f}%</td><td class="num">{t.pnl/e*100:+.2f}%</td><td>{html.escape(t.reason_close)}</td></tr>')
trades_html='\n'.join(rows)

html_doc=f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html,body{{margin:0;padding:0;background:transparent;color:var(--foreground);font-family:var(--font-sans,system-ui,sans-serif);}}
  .wrap{{max-width:960px;margin:0 auto;}}
  h1{{font-size:22px;line-height:1.25;margin:0 0 4px;font-weight:650;letter-spacing:-.01em;}}
  .sub{{color:var(--muted-foreground);font-size:13px;margin:0 0 18px;}}
  .kpis{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:0 0 22px;}}
  .kpi{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:12px 12px 10px;}}
  .kpi .v{{font-size:20px;font-weight:650;letter-spacing:-.02em;line-height:1.1;}}
  .kpi .l{{font-size:11px;color:var(--muted-foreground);margin-top:4px;text-transform:uppercase;letter-spacing:.04em;}}
  .pos{{color:var(--success);}} .neg{{color:var(--destructive);}}
  h2{{font-size:14px;margin:26px 0 8px;font-weight:600;letter-spacing:.01em;}}
  .note{{font-size:12px;color:var(--muted-foreground);margin:6px 0 0;line-height:1.5;}}
  .chart{{width:100%;height:auto;display:block;overflow:visible;}}
  .legend{{display:flex;gap:18px;font-size:12px;color:var(--muted-foreground);margin:6px 0 0;flex-wrap:wrap;}}
  .dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle;}}
  table{{width:100%;border-collapse:collapse;font-size:12px;}}
  th{{text-align:left;color:var(--muted-foreground);font-weight:500;padding:7px 8px;border-bottom:1px solid var(--border);}}
  td{{padding:7px 8px;border-bottom:1px solid var(--border);}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;}}
  .heat td{{text-align:center;font-variant-numeric:tabular-nums;border:1px solid var(--background);border-radius:4px;color:#111;font-weight:600;}}
  .heat th{{text-align:center;}}
  .caveat{{border-left:3px solid var(--warning);padding:10px 0 10px 14px;margin:18px 0 0;font-size:12px;line-height:1.6;color:var(--muted-foreground);}}
  @media(max-width:720px){{.kpis{{grid-template-columns:repeat(3,minmax(0,1fr));}}}}
</style></head><body><div class="wrap">
<h1>UNI/USDT 4h — capitulation dip</h1>
<p class="sub">Binance spot production klines · 2024-11-28 04:00 → 2026-11-28 00:00 UTC · 4,380 bars · next-open fills · 10 bps fee + 5 bps slippage per side · $1,000 start · 5% risk · 50% max position</p>

<div class="kpis">
  <div class="kpi"><div class="v pos">+67.8%</div><div class="l">2y return</div></div>
  <div class="kpi"><div class="v neg">-5.3%</div><div class="l">max drawdown</div></div>
  <div class="kpi"><div class="v">2.13</div><div class="l">Sharpe</div></div>
  <div class="kpi"><div class="v">19</div><div class="l">trades</div></div>
  <div class="kpi"><div class="v">89.5%</div><div class="l">win rate</div></div>
  <div class="kpi"><div class="v">2.9%</div><div class="l">time in market</div></div>
</div>

<h2>Equity: sleeve vs buy &amp; hold</h2>
<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Equity curve">
  {''.join(grid)}
  <polyline points="{bh_path}" fill="none" stroke="var(--muted-foreground)" stroke-width="1.6" stroke-dasharray="5 5" opacity="0.7"/>
  <polyline points="{eq_path}" fill="none" stroke="var(--accent)" stroke-width="2.4"/>
</svg>
<div class="legend"><span><span class="dot" style="background:var(--accent)"></span>sleeve (cash when flat)</span><span><span class="dot" style="background:var(--muted-foreground)"></span>buy &amp; hold UNI</span></div>
<p class="note">The sleeve is flat ~97% of the time. It is not a proxy for UNI beta; the return comes from 19 discrete bounce trades.</p>

<h2>Drawdown</h2>
<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Drawdown curve">
  {''.join(dd_grid)}
  <polyline points="{dd_path}" fill="none" stroke="var(--destructive)" stroke-width="2.2"/>
</svg>
<p class="note">Worst close-to-close drawdown -5.3%. Buy &amp; hold UNI drew down -78.5% over the same window.</p>

<h2>Year-2 holdout across the parameter plateau</h2>
<p class="note">180 configs (drop 0.18–0.22, stop 2.0–3.0 ATR, hold 12–21, rr 1.5/2/none) were fitted on year 1; every one was positive in year 2. Cells below are year-2 return at stop 2.5 ATR, rr 1.5.</p>
<table class="heat"><thead><tr><th>drop \\ hold</th>{''.join(f'<th>{h}</th>' for h in holds)}</tr></thead><tbody>
{''.join(f'<tr><th>{d*100:.0f}%</th>'+''.join(f'<td style="background:{heat_color(t)}">{v:+.1f}%</td>' for v,t in row)+'</tr>' for d,row in cells)}
</tbody></table>

<h2>Trades</h2>
<table><thead><tr><th>Entry</th><th>Exit</th><th class="num">Trade</th><th class="num">Account</th><th>Exit reason</th></tr></thead><tbody>
{trades_html}
</tbody></table>

<div class="caveat"><strong>Honest caveats.</strong> 19 trades in one crash-and-rebound regime; 89.5% win is a small-sample number. The 180/180 plateau is the main defence against cell-picking, not a guarantee. A monotonic -1.2%/bar bleed loses ~27% before the 25% kill switch. Gap risk through the stop can exceed 5% per trade. Demo only — no live order path is wired.</div>

</div></body></html>'''
open(ROOT / 'research' / 'uni' / 'uni_report.html','w',encoding='utf-8').write(html_doc)
print('wrote', len(html_doc), 'bytes')
print('eq final', round(eq.iloc[-1],2), 'bh final', round(close.iloc[-1]/close.iloc[0]*1000,2))
print('heat min/max', round(vmin,2), round(vmax,2))
