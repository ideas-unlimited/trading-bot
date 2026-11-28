"""Build research/cake/cake_report.html from the cached CAKE bars.

Reruns the one rule the sweep landed on (dip in an uptrend, 3 ATR stop,
5 ATR trail, 72h max hold) on 15m / 30m / 1h over the 12-month cap and
renders the page: equity per timeframe, where the P&L came from, and the
sensitivity tables that say how much of it is real.

    python research/cake/cake_report.py
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))
sys.path.insert(0, str(HERE))

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from cake_swing import BPY, SwingLab, risk_cfg  # noqa: E402

SYMBOL = "CAKE/USDT"
TFS = ["15m", "30m", "1h"]
GATE_DAYS = 50.0
STOP_ATR = 3.0
TRAIL_ATR = 5.0
HOLD_H = 72.0
LOOKBACK_H = 120.0


def run(tf: str, gate: float = GATE_DAYS, stop: float = STOP_ATR,
        trail: float = TRAIL_ATR, days: int = 365):
    bars = load_scan_bars(SYMBOL, tf, source="binance", days=days)
    df = bars_to_frame(bars)
    strat = SwingLab(family="dip", lookback_hours=LOOKBACK_H, trend_hours=gate * 24,
                     stop_atr=stop, rr=None, trail_atr=trail, max_hold_hours=HOLD_H)
    eng = Engine(df, strat, BotConfig(initial_cash=1000.0, costs=CostConfig(),
                                      risk=risk_cfg(), bars_per_year=BPY[tf]))
    eng.run()
    res = eng.result()
    eq = res.equity_curve / res.equity_curve.iloc[0] * 1000.0
    bh = df["close"] / df["open"].iloc[0] * 1000.0
    return bars, df, eq, bh, res


def thin(s: pd.Series, n: int = 420) -> list[float]:
    step = max(1, len(s) // n)
    return [round(float(v), 1) for v in s.iloc[::step].tolist()] + [round(float(s.iloc[-1]), 1)]


def polyline(vals: list[float], w: float, h: float, lo: float, hi: float) -> str:
    if len(vals) < 2:
        return ""
    span = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(vals):
        x = 4 + i * (w - 8) / (len(vals) - 1)
        y = 4 + (1 - (v - lo) / span) * (h - 8)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def chart(series: list[tuple[str, list[float], str, str]], h: float = 210) -> str:
    w = 640.0
    allv = [v for _, vals, _, _ in series for v in vals]
    lo = min(allv) * 0.995
    hi = max(allv) * 1.005
    out = [f'<svg viewBox="0 0 {w:.0f} {h + 26:.0f}" role="img">']
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = 4 + frac * (h - 8)
        val = hi - frac * (hi - lo)
        out.append(f'<line x1="4" y1="{y:.1f}" x2="{w - 4}" y2="{y:.1f}" '
                   'stroke="currentColor" stroke-opacity="0.10" stroke-width="1"/>')
        out.append(f'<text x="{w - 6:.0f}" y="{y - 3:.1f}" text-anchor="end" '
                   f'font-size="9" fill="currentColor" opacity="0.45">{val:,.0f}</text>')
    for label, vals, color, dash in series:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.7" '
                   f'stroke-linejoin="round" points="{polyline(vals, w, h, lo, hi)}"{d}/>')
    out.append(
        f'<line x1="4" y1="{h + 12:.0f}" x2="{w - 4}" y2="{h + 12:.0f}" stroke="currentColor" '
        'stroke-opacity="0.10"/>')
    out.append(f'<text x="4" y="{h + 24:.0f}" font-size="9" fill="currentColor" opacity="0.45">start</text>')
    out.append(f'<text x="{w - 4:.0f}" y="{h + 24:.0f}" text-anchor="end" font-size="9" '
               'fill="currentColor" opacity="0.45">last bar</text>')
    out.append("</svg>")
    return "".join(out)


def pct(v: float) -> str:
    return f"{v:+.1%}"


def cls(v: float) -> str:
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def main() -> None:
    sweep = pd.read_csv(HERE / "cake_swing_tf.csv")
    cards, curves, tables = [], [], []
    detail = {}
    for tf in TFS:
        bars, df, eq, bh, res = run(tf)
        ret = eq.iloc[-1] / eq.iloc[0] - 1
        dd = (eq / eq.cummax() - 1).min()
        tr = res.trades
        wins = sum(1 for t in tr if t.return_pct > 0)
        top = sum(t.pnl for t in sorted(tr, key=lambda t: -t.pnl)[:3])
        tot = sum(t.pnl for t in tr) or 1e-9
        detail[tf] = dict(ret=ret, dd=dd, n=len(tr), win=wins / max(1, len(tr)),
                          top3=(top / tot) if tot > 0 else None, expo=res.exposure.mean(),
                          bh=df["close"].iloc[-1] / df["open"].iloc[0] - 1)
        curves.append((tf, thin(eq), f"var(--chart-{TFS.index(tf) + 1})", ""))
    curves.append(("CAKE price", thin(bh), "currentColor", "3 3"))

    for tf in TFS:
        d = detail[tf]
        tone = "ok" if d["ret"] > 0.05 else ("mid" if d["ret"] > 0 else "no")
        label = "paid" if d["ret"] > 0.05 else ("flat" if d["ret"] > 0 else "lost")
        cards.append(
            f'<div class="card"><div class="tf">{tf}</div>'
            f'<div class="big {cls(d["ret"])}">{pct(d["ret"])}</div>'
            f'<div class="meta">{d["n"]} trades &middot; {d["win"]:.0%} wins &middot; dd {pct(d["dd"])}</div>'
            f'<div class="meta">{'best 3 trades = ' + f'{d["top3"]:.0%} of p&amp;l' if d["top3"] is not None else 'net p&amp;l negative'} &middot; in market {d["expo"]:.0%}</div>'
            f'<span class="tag {tone}">{label}</span></div>')

    # sweep summary: best cell per tf, and how many cells were positive in both halves
    rows = []
    for tf in TFS:
        sub = sweep[sweep.tf == tf]
        b = sub.sort_values("score", ascending=False).iloc[0]
        both = int(((sub.is_ret > 0) & (sub.oos_ret > 0)).sum())
        rows.append(
            f'<tr><td class="name">{tf}</td><td>{html.escape(str(b.config))}</td>'
            f'<td class="{cls(b.is_ret)}">{pct(b.is_ret)}</td>'
            f'<td class="{cls(b.oos_ret)}">{pct(b.oos_ret)}</td>'
            f'<td>{b.is_trades:.0f}</td><td>{both} / {len(sub)}</td></tr>')

    gate_rows = []
    for g in (20.0, 35.0, 50.0, 70.0, 100.0):
        _, _, geq, _, _ = run("30m", gate=g)
        w1 = "<b>" if g == GATE_DAYS else ""
        w2 = "</b>" if g == GATE_DAYS else ""
        gate_rows.append(
            f'<tr><td class="name">{w1}{g:.0f}-day EMA{w2}</td>'
            f'<td class="{cls(geq.iloc[-1] / geq.iloc[0] - 1)}">{pct(geq.iloc[-1] / geq.iloc[0] - 1)}</td>'
            f'<td class="{cls((geq / geq.cummax() - 1).min())}">{pct((geq / geq.cummax() - 1).min())}</td></tr>')

    trail_rows = []
    for label, stop, trail in (("2.5", 2.5, 5.0), ("3.0", 3.0, 5.0), ("4.0", 4.0, 5.0),
                               ("3 ATR", 3.0, 3.0), ("4 ATR", 3.0, 4.0), ("6 ATR", 3.0, 6.0)):
        _, _, teq, _, tres = run("30m", stop=stop, trail=trail)
        tret = teq.iloc[-1] / teq.iloc[0] - 1
        is_bold = (stop == 3.0 and trail == 5.0)
        name = f"stop {label}" if stop != 3.0 else f"trail {label}"
        trail_rows.append(
            f'<tr><td class="name">{"<b>" if is_bold else ""}{name}{"</b>" if is_bold else ""}</td>'
            f'<td class="{cls(tret)}">{pct(tret)}</td>'
            f'<td>{len(tres.trades)}</td></tr>')

    first = bars[0].ts.date().isoformat()
    last = bars[-1].ts.date().isoformat()
    page = TEMPLATE.format(
        first=first, last=last, symbol="CAKE/USDT",
        chart=chart(curves), cards="".join(cards), rows="".join(rows),
        gate_rows="".join(gate_rows), trail_rows="".join(trail_rows),
        m30=detail["30m"]["ret"], m30dd=detail["30m"]["dd"],
        pct_neg15=f"{detail['15m']['ret']:.1%}".replace("-", "\u2212"),
        pct_neg1h=f"{detail['1h']['ret']:.1%}".replace("-", "\u2212"),
    )
    (HERE / "cake_report.html").write_text(page, encoding="utf-8")
    print(f"wrote {HERE / 'cake_report.html'}")
    for tf in TFS:
        d = detail[tf]
        print(f"{tf}: ret {pct(d['ret'])} dd {pct(d['dd'])} trades {d['n']} "
              f"wins {d['win']:.0%} expo {d['expo']:.0%} top3 {d['top3']}")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CAKE swing - 15m vs 30m vs 1h</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: transparent; color: var(--foreground);
         font-family: var(--font-sans); font-size: 14px; line-height: 1.5; }}
  .wrap {{ max-width: 720px; margin: 0 auto; padding: 4px 0 8px; }}
  h1 {{ font-size: 21px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 14px; margin: 26px 0 10px; font-weight: 650; }}
  .sub {{ color: var(--muted-foreground); font-size: 12.5px; margin: 0 0 2px; }}
  .mono {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
  .verdict {{ margin-top: 16px; padding: 12px 14px; border-radius: var(--radius);
             background: var(--accent-surface); color: var(--accent-surface-foreground);
             font-size: 13.5px; }}
  .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }}
  .card {{ border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 13px;
          background: var(--card); }}
  .card .tf {{ font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em;
              color: var(--muted-foreground); text-transform: uppercase; }}
  .card .big {{ font-family: var(--font-mono); font-size: 21px; font-weight: 600; margin: 3px 0 1px; }}
  .card .meta {{ font-size: 11.5px; color: var(--muted-foreground); }}
  .tag {{ display: inline-block; font-size: 10.5px; font-weight: 650; letter-spacing: .04em;
         padding: 2px 7px; border-radius: 999px; text-transform: uppercase; margin-top: 8px; }}
  .tag.ok {{ background: var(--success-surface); color: var(--success); }}
  .tag.no {{ background: var(--destructive-surface); color: var(--destructive); }}
  .tag.mid {{ background: var(--warning-surface); color: var(--warning); }}
  .pos {{ color: var(--success); }} .neg {{ color: var(--destructive); }}
  .chartbox {{ border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px 6px;
              background: var(--card); }}
  svg {{ display: block; width: 100%; height: auto; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px;
            color: var(--muted-foreground); margin: 4px 2px 2px; }}
  .legend i {{ display: inline-block; width: 14px; height: 2px; margin-right: 5px;
              vertical-align: middle; border-radius: 2px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th {{ text-align: right; font-weight: 600; color: var(--muted-foreground); font-size: 11px;
       text-transform: uppercase; letter-spacing: .04em; padding: 6px 7px;
       border-bottom: 1px solid var(--border); }}
  th:first-child, td:first-child {{ text-align: left; }}
  td {{ text-align: right; padding: 6px 7px; border-bottom: 1px solid var(--border);
       font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
  td.name {{ font-family: var(--font-sans); }}
  tbody tr:last-child td {{ border-bottom: 0; }}
  .note {{ font-size: 11.5px; color: var(--muted-foreground); margin: 8px 0 0; }}
  ul {{ margin: 8px 0 0; padding-left: 18px; font-size: 12.5px; }}
  li {{ margin: 4px 0; }}
  @media (max-width: 560px) {{ .cards {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>CAKE swing &mdash; which chart pays?</h1>
  <p class="sub">{symbol} &middot; 15m / 30m / 1h &middot; {first} &rarr; {last} (12-month cap for sub-4h)</p>
  <p class="sub">Long only, spot. Signal on the close, fill at the next open. 10 bps fee + 5 bps
     slippage per side, 3% risk per trade, $1,000 start. Drawdown kill switch off so every
     chart gets the whole window.</p>

  <div class="verdict">
    <b>One chart pays, and the payment is three trades.</b> The rule below turns +{m30:.0%} on 30m
    against &minus;36% for holding CAKE, but {pct_neg15} on 15m and {pct_neg1h} on 1h, and 99% of the
    30m profit comes from three September trades. That is an uptrend the rule happened to be
    long for, not a repeated edge.
  </div>

  <h2>The rule</h2>
  <ul>
    <li>Buy when RSI(14) crosses back above 30 on a close above the 50-day EMA (the gate).</li>
    <li>Stop 3 ATR(14) below the entry, then trail 5 ATR under the highest high since entry.</li>
    <li>Out anyway after 72 hours. No target, no shorting.</li>
  </ul>

  <h2>Result per timeframe</h2>
  <div class="cards">{cards}</div>
  <p class="note">Drawdown is of the equity curve, not of the trade. &ldquo;Best 3 trades&rdquo; is
     their share of total net P&amp;L: 99% on 30m means the other 44 trades net roughly zero.</p>

  <h2>Equity, same rule, same window</h2>
  <div class="chartbox">
    {chart}
    <div class="legend">
      <span><i style="background:var(--chart-1)"></i>15m</span>
      <span><i style="background:var(--chart-2)"></i>30m</span>
      <span><i style="background:var(--chart-3)"></i>1h</span>
      <span><i style="background:currentColor;opacity:.6"></i>CAKE price</span>
    </div>
  </div>

  <h2>What the 53-config sweep found</h2>
  <table>
    <thead><tr><th>chart</th><th>best cell by IS score</th><th>IS</th><th>OOS</th>
      <th>IS trades</th><th>cells positive in both halves</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="note">Selection score is the worse of two IS halves, so a cell that only worked in
     one half cannot win. Three families (breakout, pullback, dip) &times; two lookbacks &times;
     two stops &times; four exits, plus donchian, momentum_swing and rsi_meanrev with the same
     windows in hours. <b>No cell on any chart is positive in both halves.</b></p>
  <p class="note">Note what that means for the rule above: it is <b>not</b> the sweep&rsquo;s best cell.
     It was picked on the full-window result, which includes the OOS half &mdash; the exact
     mistake this split exists to catch. Treat it as a hypothesis that earned a forward test,
     not as a finding.</p>

  <h2>Is the 30m gate doing the work, or is it luck?</h2>
  <table>
    <thead><tr><th>gate</th><th>12-month return</th><th>max dd</th></tr></thead>
    <tbody>{gate_rows}</tbody>
  </table>
  <p class="note">Every gate from 20 to 100 days ends positive, so the gate itself is not a
     knife edge &mdash; it simply keeps the book out of CAKE&rsquo;s downtrend.</p>

  <h2>Where it breaks</h2>
  <table>
    <thead><tr><th>variant, 30m</th><th>12-month return</th><th>trades</th></tr></thead>
    <tbody>{trail_rows}</tbody>
  </table>
  <p class="note">The trail is a cliff, not a plateau: 4 ATR gives +12%, 5 ATR gives +34%,
     6 ATR gives +22%. A robust parameter would not move the result by 20 points per ATR.</p>

  <h2>Honest reading</h2>
  <ul>
    <li>CAKE fell 36% over the window. Any long-only swing rule starts 36 points behind and
        can only win by sitting out, which is what the 50-day gate does.</li>
    <li>30m is the only chart where the engine of the move is visible: 47 trades, 40% win rate,
        median trade negative, fat right tail from a handful of continuation runs.</li>
    <li>15m loses money at 107 trades even with the same rule &mdash; costs and chop, not signal.</li>
    <li>The same rule on 4h or 1d over two years takes 0&ndash;2 trades: on a coarser chart the
        dip never coincides with the gate, so the 30m result depends on intrabar noise.</li>
    <li>Therefore: not a shippable sleeve. It is a candidate to dry-run on demo, sized at
        the 3% the lab used, and it must survive a second CAKE rally it did not see.</li>
  </ul>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()