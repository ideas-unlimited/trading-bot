"""Render research/allcoins_report.html from the study CSVs."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import BAR_HOURS, LOAD_DAYS, WINDOW_DAYS, grids  # noqa: E402
from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402

TOP5 = [
    ("NEAR/USDT", "4h", "sma_cross 96/384h"),
    ("NEAR/USDT", "4h", "momentum_swing 120/480h t5.0"),
    ("NEAR/USDT", "4h", "lab pullback 120h s2 2R"),
    ("RENDER/USDT", "4h", "lab breakout 120h s2 t3"),
    ("ARB/USDT", "4h", "lab breakout 120h s2 t3"),
]
ONE_PER_COIN = [
    ("NEAR/USDT", "lab pullback 120h s2 2R"),
    ("RENDER/USDT", "lab breakout 120h s2 t3"),
    ("ARB/USDT", "lab breakout 120h s2 t3"),
    ("PYTH/USDT", "lab pullback 120h s2 2R"),
    ("JUP/USDT", "capdip 240h d20 rr1.5"),
]
CFGS = [
    "lab breakout 120h s3 t5",
    "lab breakout 120h s2 t3",
    "lab pullback 120h s2 2R",
    "momentum_swing 240/960h t3.5",
    "vol_climax z2 d6 hold48h",
    "sma_cross 96/384h",
    "donchian 240/96h",
    "capdip 240h d20 rr1.5",
]


def pct(v, digits=1):
    if v is None or pd.isna(v):
        return "-"
    return f"{v * 100:+.{digits}f}%"


def equity_curve(symbol, label, tf="4h"):
    df = bars_to_frame(load_scan_bars(symbol, tf, source="binance", days=LOAD_DAYS))
    start = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    strat = dict(grids(tf))[label]
    if hasattr(strat, "reset"):
        strat.reset()
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(0.03, 0.95, max_drawdown_stop=0.99),
                    bars_per_year=365.0 * 24.0 / BAR_HOURS[tf])
    eng = Engine(df, strat, cfg)
    eng.run()
    eq = eng.result().equity_curve
    eq = eq[eq.index >= start]
    return eq / eq.iloc[0]


def table(headers, rows, cls=""):
    head = "".join("<th>" + h + "</th>" for h in headers)
    body = "".join("<tr>" + "".join("<td>" + c + "</td>" for c in r) + "</tr>" for r in rows)
    return "<table class=" + chr(34) + cls + chr(34) + "><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"
PALETTE = ["#2f6f4f", "#b5762a", "#3f6f9a", "#8a4f7d", "#6f6f6f"]


def svg_curves(curves, w=700, h=280):
    pad_l, pad_r, pad_b, pad_t = 44, 118, 40, 12
    lo = min(float(s.min()) for _, s in curves)
    hi = max(float(s.max()) for _, s in curves)
    span = max(hi - lo, 1e-9)
    t0 = min(s.index[0] for _, s in curves)
    t1 = max(s.index[-1] for _, s in curves)
    dur = (t1 - t0).total_seconds() or 1.0

    def x(t):
        return pad_l + (t - t0).total_seconds() / dur * (w - pad_l - pad_r)

    def y(v):
        return pad_t + (hi - v) / span * (h - pad_t - pad_b)

    p = ["<svg viewBox=\"0 0 %d %d\" width=\"100%%\">" % (w, h)]
    for i in range(5):
        v = lo + span * i / 4
        p.append("<line x1=\"%d\" x2=\"%d\" y1=\"%.1f\" y2=\"%.1f\" class=\"grid\"/>" % (pad_l, w - pad_r, y(v), y(v)))
        p.append("<text x=\"%d\" y=\"%.1f\" text-anchor=\"end\" class=\"tick\">%.2fx</text>" % (pad_l - 6, y(v) + 4, v))
    p.append("<line x1=\"%d\" x2=\"%d\" y1=\"%.1f\" y2=\"%.1f\" class=\"base\"/>" % (pad_l, w - pad_r, y(1.0), y(1.0)))
    labels = []
    for i, (name, s) in enumerate(curves):
        pts = " ".join("%.1f,%.1f" % (x(t), y(v)) for t, v in s.items())
        color = "var(--chart-%d, %s)" % (i + 1, PALETTE[i % len(PALETTE)])
        p.append("<polyline points=\"%s\" fill=\"none\" stroke=\"%s\" stroke-width=\"1.6\"/>" % (pts, color))
        labels.append([y(float(s.iloc[-1])) + 3.5, name, color])
    labels.sort()
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < 12.0:
            labels[i][0] = labels[i - 1][0] + 12.0
    for yy, name, color in labels:
        p.append("<text x=\"%d\" y=\"%.1f\" fill=\"%s\" class=\"lbl\">%s</text>" % (w - pad_r + 6, yy, color, name))
    for frac in (0.0, 0.5, 1.0):
        t = t0 + (t1 - t0) * frac
        p.append("<text x=\"%.1f\" y=\"%d\" text-anchor=\"middle\" class=\"tick\">%s</text>" % (x(t), h - 10, t.strftime("%b %y")))
    p.append("</svg>")
    return "".join(p)


def svg_breadth(rows, w=700):
    labels = {
        "lab breakout 120h s3 t5": "breakout s3 t5",
        "lab breakout 120h s2 t3": "breakout s2 t3",
        "lab pullback 120h s2 2R": "pullback s2 2R",
        "momentum_swing 240/960h t3.5": "momentum 240/960",
        "vol_climax z2 d6 hold48h": "vol_climax z2 d6",
        "sma_cross 96/384h": "sma_cross 96/384",
        "donchian 240/96h": "donchian 240/96",
        "capdip 240h d20 rr1.5": "capdip d20 rr1.5",
    }
    rowh, gap, top = 34, 8, 30
    h = top + len(rows) * (rowh + gap) + 6
    bar_x, bar_w = 210, w - 210 - 92
    p = ["<svg viewBox=\"0 0 %d %d\" width=\"100%%\">" % (w, h)]
    p.append("<text x=\"%d\" y=\"12\" class=\"tick\">green = 4h chart, orange = 1d chart, share of coins positive</text>" % bar_x)
    p.append("<line x1=\"%d\" x2=\"%d\" y1=\"%d\" y2=\"%d\" class=\"grid\"/>" % (bar_x, bar_x, top - 6, h - 4))
    for i, (label, p4, p1) in enumerate(rows):
        yy = top + i * (rowh + gap)
        name = labels.get(label, label)
        p.append("<text x=\"%d\" y=\"%d\" text-anchor=\"end\" class=\"lbl\">%s</text>" % (bar_x - 10, yy + 12, name))
        w4 = bar_w * max(p4, 0) if p4 == p4 else 0.0
        w1 = bar_w * max(p1, 0) if p1 == p1 else 0.0
        p.append("<rect x=\"%d\" y=\"%d\" width=\"%.1f\" height=\"10\" fill=\"var(--chart-1, #2f6f4f)\" opacity=\"0.8\"/>" % (bar_x, yy + 1, w4))
        p.append("<rect x=\"%d\" y=\"%d\" width=\"%.1f\" height=\"10\" fill=\"var(--chart-2, #b5762a)\" opacity=\"0.8\"/>" % (bar_x, yy + 13, w1))
        t4 = ("%.0f%%" % (p4 * 100)) if p4 == p4 else "n/a"
        t1 = ("%.0f%%" % (p1 * 100)) if p1 == p1 else "n/a"
        p.append("<text x=\"%d\" y=\"%d\" class=\"tick\">%s</text>" % (bar_x + bar_w + 6, yy + 10, t4))
        p.append("<text x=\"%d\" y=\"%d\" class=\"tick\">%s</text>" % (bar_x + bar_w + 6, yy + 22, t1))
    p.append("</svg>")
    return "".join(p)


def main():
    d = pd.read_csv(HERE / "allcoins_stage1.csv")
    ok = d[(d.ret.notna()) & (d.trades >= 8)]
    s2 = pd.read_csv(HERE / "allcoins_stage2.csv")
    s2p = pd.read_csv(HERE / "allcoins_stage2_picks.csv")
    rot = pd.read_csv(HERE / "rotation.csv")
    grid = pd.read_csv(HERE / "grid_ladder.csv")
    gates = pd.read_csv(HERE / "gates.csv")
    gates["calmar"] = gates.ret / gates.dd.abs().replace(0, float("nan"))

    corr = pd.concat([s2, s2p]).drop_duplicates(subset=["symbol", "config"])
    corr = corr.set_index(["symbol", "config"])

    def row_of(sym, config, tf="4h"):
        hit = d[(d.symbol == sym) & (d.config == config) & (d.tf == tf)]
        return hit.iloc[0] if len(hit) else None

    def breadth(config, tf):
        x = ok[(ok.config == config) & (ok.tf == tf)]
        return float((x.ret > 0).mean()) if len(x) else float("nan")

    def extra(sym, config):
        if (sym, config) in corr.index:
            r = corr.loc[(sym, config)]
            return "%d/%d" % (int(r.tfs_positive), int(r.tfs_tested)), pct(r.neigh_median)
        return "n/t", "-"

    top_rows = []
    for sym, tf, config in TOP5:
        r = row_of(sym, config, tf)
        tfs, nm = extra(sym, config)
        top_rows.append(["<b>%s</b>" % sym.split("/")[0], config,
                         "<b>%s</b>" % pct(r.ret), pct(r.dd), str(int(r.trades)),
                         pct(r.is_ret), pct(r.oos_ret), "%s" % pct(breadth(config, tf), 0),
                         "%s<br><span class=\"dim\">%s</span>" % (tfs, nm)])

    one_rows = []
    for sym, config in ONE_PER_COIN:
        r = row_of(sym, config)
        tfs, nm = extra(sym, config)
        win = r.wins / r.trades if r.trades else float("nan")
        one_rows.append(["<b>%s</b>" % sym.split("/")[0], config, "<b>%s</b>" % pct(r.ret),
                         pct(r.dd), str(int(r.trades)), "%.0f%%" % (win * 100),
                         pct(r.is_ret), pct(r.oos_ret), tfs])

    curves = []
    for sym, tf, config in TOP5:
        try:
            eq = equity_curve(sym, config, tf)
            curves.append(("%s %s" % (sym.split("/")[0], pct(float(eq.iloc[-1] - 1), 0)), eq))
        except Exception as exc:
            print("curve failed", sym, config, exc)

    breadth_rows = []
    for c in CFGS:
        short = c.replace(" 120h", "").replace("240h ", "")
        breadth_rows.append((short, breadth(c, "4h"), breadth(c, "1d")))

    gg = grid.groupby("tf").agg(n=("ret", "size"), pos=("ret", lambda s: (s > 0).mean()),
                                med=("ret", "median"))
    rot_pos = float((rot.ret > 0).mean())
    rot_best = rot.sort_values("ret", ascending=False).iloc[0]
    gates_none = gates[gates.gate == "none"]
    gates_btc = gates[gates.gate == "btc_trend"]
    calmar_win = int((gates_btc.set_index(["symbol", "sleeve"]).calmar
                      > gates_none.set_index(["symbol", "sleeve"]).calmar).sum())

    total_cells = len(d)
    pos_share = float((ok.ret > 0).mean())
    beat_bh = float((ok.ret > ok.bh).mean())
    both_halves = float(((ok.is_ret > 0) & (ok.oos_ret > 0)).mean())

    import json
    payload = {
        "total": total_cells, "pos_share": pos_share, "beat_bh": beat_bh,
        "both_halves": both_halves, "top_rows": top_rows, "one_rows": one_rows,
        "curves": [c[0] for c in curves], "breadth_rows": [[b[0], b[1], b[2]] for b in breadth_rows],
        "grid": {k: [float(v.n), float(v.pos), float(v.med)] for k, v in gg.iterrows()},
        "rot_pos": rot_pos, "rot_best": {k: (float(v) if not isinstance(v, str) else v) for k, v in rot_best.items()},
        "calmar_win": calmar_win, "n_sleeves": len(gates_none),
        "dd_none": float(gates_none.dd.mean()), "dd_btc": float(gates_btc.dd.mean()),
    }
    (HERE / "allcoins_payload.json").write_text(json.dumps(payload, indent=1))
    svg_c = svg_curves(curves)
    svg_b = svg_breadth(breadth_rows)
    (HERE / "allcoins_charts.html").write_text(svg_c + "\n<!--split-->\n" + svg_b)
    print("data ok:", total_cells, "cells,", len(curves), "curves")


def render():
    import json
    p = json.loads((HERE / "allcoins_payload.json").read_text())
    svg_c, svg_b = (HERE / "allcoins_charts.html").read_text().split("<!--split-->")

    def tbl(headers, rows, cls=""):
        head = "".join("<th>" + h + "</th>" for h in headers)
        body = "".join("<tr>" + "".join("<td>" + c + "</td>" for c in r) + "</tr>" for r in rows)
        return ("<table class=%s><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                % (chr(34) + cls + chr(34), head, body))

    html = TEMPLATE
    _map = {
        "total": format(p["total"], ","),
        "pos_share": "%.0f%%" % (p["pos_share"] * 100),
        "beat_bh": "%.0f%%" % (p["beat_bh"] * 100),
        "both_halves": "%.0f%%" % (p["both_halves"] * 100),
        "top_table": tbl(["Coin", "Config (4h)", "Year", "Max DD", "Trades", "H1", "H2", "Coins +", "Charts"],
                         p["top_rows"], "pick"),
        "one_table": tbl(["Coin", "Config", "Year", "Max DD", "Trades", "Win", "H1", "H2", "Charts"],
                         p["one_rows"], "pick"),
        "curves": svg_c,
        "breadth": svg_b,
        "grid_4h": grid_line(p["grid"], "4h"),
        "grid_1d": grid_line(p["grid"], "1d"),
        "rot_pos": "%.0f%%" % (p["rot_pos"] * 100),
        "rot_best": "%.1f%%" % (p["rot_best"]["ret"] * 100),
        "rot_dd": "%.0f%%" % (p["rot_best"]["dd"] * 100),
        "calmar_win": str(p["calmar_win"]),
        "n_sleeves": str(p["n_sleeves"]),
        "dd_none": "%.0f%%" % (p["dd_none"] * 100),
        "dd_btc": "%.0f%%" % (p["dd_btc"] * 100),
    }
    for _k, _v in _map.items():
        html = html.replace("%" + "(" + _k + ")s", _v)
    html = html.replace("%%", "%")
    (HERE / "allcoins_report.html").write_text(html)
    print("wrote", HERE / "allcoins_report.html", len(html), "bytes")


def grid_line(g, tf):
    x = g.get(tf)
    if not x:
        return "-"
    return "%d runs, %.0f%% positive, median %+.1f%%" % (int(x[0]), x[1] * 100, x[2] * 100)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-coin strategy sweep, one year</title>
<style>
  html, body { margin: 0; background: var(--background, #ffffff); color: var(--foreground, #14161a);
    font-family: var(--font-sans, system-ui, sans-serif); }
  .wrap { max-width: 720px; margin: 0 auto; padding: 2px 0 20px; }
  h1 { font-size: 1.42rem; line-height: 1.2; margin: 0 0 4px; letter-spacing: -0.015em; }
  .sub { color: var(--muted-foreground, #6b7280); font-size: 0.84rem; line-height: 1.5; margin: 0 0 16px; }
  h2 { font-size: 0.98rem; margin: 24px 0 6px; letter-spacing: -0.005em; }
  p { font-size: 0.89rem; line-height: 1.55; margin: 8px 0; }
  .dim { color: var(--muted-foreground, #6b7280); }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; margin: 12px 0 2px; }
  .stat { background: var(--card, #f7f7f8); border: 1px solid var(--border, #e4e5e9); border-radius: var(--radius, 8px); padding: 9px 11px; }
  .stat b { display: block; font-size: 1.18rem; letter-spacing: -0.02em; }
  .stat span { color: var(--muted-foreground, #6b7280); font-size: 0.7rem; line-height: 1.35; display: block; margin-top: 3px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.76rem; margin: 6px 0 2px; }
  th { text-align: right; font-weight: 600; color: var(--muted-foreground, #6b7280); font-size: 0.64rem;
    text-transform: uppercase; letter-spacing: 0.045em; padding: 4px 4px; border-bottom: 1px solid var(--border, #e4e5e9); white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  th:nth-child(2), td:nth-child(2) { text-align: left; }
  td { text-align: right; padding: 5px 4px; border-bottom: 1px solid var(--border, #e4e5e9);
    font-variant-numeric: tabular-nums; }
  tbody tr:last-child td { border-bottom: none; }
  .note { color: var(--muted-foreground, #6b7280); font-size: 0.72rem; line-height: 1.45; margin: 5px 0 0; }
  .grid { stroke: var(--border, #e4e5e9); stroke-width: 1; stroke-dasharray: 2 3; }
  .base { stroke: var(--muted-foreground, #6b7280); stroke-width: 1; opacity: 0.5; }
  .tick { fill: var(--muted-foreground, #6b7280); font-size: 9px; }
  .lbl { fill: var(--foreground, #14161a); font-size: 9px; }
  .verdict { border: 1px solid var(--border, #e4e5e9); border-left: 3px solid var(--accent, #2f6f4f); background: var(--card, #f7f7f8);
    border-radius: var(--radius, 8px); padding: 11px 13px; margin: 16px 0 4px; }
  .verdict p { margin: 4px 0; }
  ul { margin: 6px 0; padding-left: 17px; }
  li { font-size: 0.86rem; line-height: 1.5; margin: 4px 0; }
  .flag { color: var(--warning, #b8860b); }
</style></head><body><div class="wrap">

<h1>Every coin, every strategy, one year</h1>
<p class="sub">30 Binance spot pairs &middot; 26 configurations &middot; 4h and 1d &middot; %(total)s cells &middot;
window 2025-11-28 to 2026-11-28 &middot; signals at the close, fills at the next open, 10 bps fee + 5 bps slippage each side.</p>

<div class="stats">
  <div class="stat"><b>%(pos_share)s</b><span>of cells with 8+ trades ended positive</span></div>
  <div class="stat"><b>%(beat_bh)s</b><span>beat buying and holding the same coin</span></div>
  <div class="stat"><b>%(both_halves)s</b><span>positive in both halves of the year</span></div>
  <div class="stat"><b>56%%</b><span>best share of coins any 4h config won on</span></div>
</div>

<h2>Top five by profit</h2>
%(top_table)s
<p class="note">H1 and H2 are the two halves of the year, measured on one causal run. "Coins +" is the share of
all 29-30 coins the same configuration was profitable on, at that chart. "Charts" is how many of
1h / 4h / 6h / 12h / 1d the cell also made money on, and the small number under it is the median return
of its eight nearest parameter neighbours. A real edge survives both; a fitted one does not.</p>



<h2>The five curves, same $1,000, same year</h2>
%(curves)s
<p class="note">Three of the five are NEAR, which is what "top five" honestly looks like on a one-year screen:
the coin matters as much as the rule. NEAR fell 20.6 percent over the year and the 4h sleeves still made
68 to 87 percent on it, by sitting out the declines rather than riding them.</p>

<h2>If it has to be one coin each</h2>
%(one_table)s
<p class="note">One sleeve per coin, same rule: 4h, both halves positive, 15+ trades. PYTH and JUP are here
because their extra trades (66 and 35) make the result less of a single-trade accident than the top-five cells,
not because their return is higher.</p>

<h2>The rule has to work on the other 28 coins too</h2>
%(breadth)s
<p>Breadth is the test that separates a rule from a fitted curve. Every one of the top-five cells is a rule
that was <em>not</em> profitable on most coins: the NEAR pullback made money on 21 percent of them, and no 4h
configuration cleared 56 percent. At the daily chart the same pullback and breakout logic flips:
<strong>lab breakout 120h s3 t5 was positive on 83 percent of 29 coins</strong> (median +2.9 percent, average
drawdown -5.7), and s2 t3 on 69 percent. Small per coin, but it is the only thing in 1,534 cells that looks
like a market property rather than a coin property.</p>
<p class="note">Trades matter here: those 1d breakout runs average 16.6 trades a year against 10 to 20 for the
4h cells, and only 8 of 29 were positive in both halves, so the edge is thin and it leans on the second-half
rally. It is a family to build on, not a finished sleeve.</p>

<h2>The strategy families from the two lists</h2>
<p>Everything above is trend, breakout, pullback, momentum and mean reversion. The lists added seven more
families; four are testable on long-only spot with the data we keep, and I ran them:</p>
<table><thead><tr><th>Family</th><th>What I ran</th><th>Result</th></tr></thead><tbody>
<tr><td>Momentum ranking<br><span class="dim">cross-sectional</span></td>
<td>28 coins, rank by 20/40/60-day return, hold top 1/2/3/5, daily or weekly, optional 100-day filter, 72 configs</td>
<td>%(rot_pos)s positive. Best: daily, 20-day lookback, top-2, %(rot_best)s with a %(rot_dd)s drawdown and 154x turnover. Equal-weight buy-and-hold was -39.8 percent and BTC -33.8.</td></tr>
<tr><td>Grid trading<br><span class="dim">long-only ladder</span></td>
<td>920 runs: buy step percent under a 30/90-day high, 4 or 6 levels, take profit at 3 or 5 percent</td>
<td>4h: %(grid_4h)s. 1d: %(grid_1d)s. The article called it: a ladder buys inventory into a downtrend and never gets the bounce it priced.</td></tr>
<tr><td>Regime gating<br><span class="dim">BTC trend filter</span></td>
<td>The eight best sleeves re-run with entries blocked while BTC is under its 100-day average</td>
<td>Drawdown -%(dd_none)s to -%(dd_btc)s on average, but Calmar better on only %(calmar_win)s of %(n_sleeves)s sleeves: it cuts the risk and the return together.</td></tr>
<tr><td>Lead-lag gate<br><span class="dim">BTC correlation</span></td>
<td>Same eight sleeves, entries blocked while 30-day correlation to BTC is above 0.85</td>
<td>Better on 2 of 8 returns. No usable signal.</td></tr>
</tbody></table>
<p class="note">Not testable here, and worth saying why: funding-rate arbitrage and market making need perps,
shorts or maker rebates the spot book does not have; liquidation cascades need open-interest and funding data
we do not store; statistical arbitrage in its real form needs shorts for the other leg. A long-only
"buy the laggard of the pair" variant is the only piece that fits, and it is a coin-pair study of its own.</p>

<div class="verdict">
<p><strong>Where this leaves the year.</strong> The most profitable cells are 4h NEAR sleeves (+68 to +87 percent,
drawdowns of 15 to 30) and they are coin-specific: the rules lose on most other coins. The only broad edge is
the 1d breakout family, 69 to 83 percent of coins positive with a +3 percent median, too small to pay a living
alone but the right base for the next step.</p>
<p><strong>What I would ship first:</strong> JUP capdip 240h d20 rr1.5 (+28.1 percent, 35 trades, 63 percent win,
-9.0 drawdown, positive on all five charts and identical to its neighbour median) as the one sleeve with
evidence at its back, then the 1d breakout as a separate small size until it earns more.</p>
<p><strong>What not to ship:</strong> NEAR sma_cross 96/384h - +87 percent is ten trades, one of which is most of
the return, and its first half was negative. That is a lottery ticket priced as a result.</p>
</div>

<p class="note">Scripts: research/allcoins_sweep.py (stage 1), allcoins_deep.py (cross-chart and neighbour checks),
rotation.py, grid_ladder.py, gates.py. Data: 4h and 1d Binance spot from the local bars cache, 455 days loaded,
365 measured. Costs inside every number.</p>

</div></body></html>
"""


if __name__ == "__main__":
    main()
    render()
