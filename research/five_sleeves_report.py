"""Render research/five_sleeves.html from the pick table and the book JSON."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent

PALETTE = ["#2f6f4f", "#b5762a", "#3f6f9a", "#8a4f7d", "#6f6f6f"]
SHORT = {"NEAR/USDT": "NEAR", "RENDER/USDT": "RENDER", "ARB/USDT": "ARB",
         "PYTH/USDT": "PYTH", "JUP/USDT": "JUP"}


def pc(v, dp: int = 1, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "\u2013"
    return (f"{v * 100:+.{dp}f}%" if sign else f"{v * 100:.{dp}f}%")


def num(v, dp: int = 2) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "\u2013"
    return f"{v:.{dp}f}"


def _scale(v, lo, hi, a, b):
    if hi == lo:
        return (a + b) / 2
    return a + (v - lo) / (hi - lo) * (b - a)


def nice_ticks(lo: float, hi: float, n: int = 5) -> list:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    start = math.ceil(lo / step) * step
    out = []
    v = start
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def svg_multi(curves: dict, w: int = 700, h: int = 300) -> str:
    pad_l, pad_r, pad_t, pad_b = 46, 96, 14, 26
    x0, x1 = pad_l, w - pad_r
    y0, y1 = pad_t, h - pad_b
    days = sorted({d for c in curves.values() for d, _ in c})
    if not days:
        return ""
    d0 = date.fromisoformat(days[0])
    d1 = date.fromisoformat(days[-1])
    span = max(1, (d1 - d0).days)
    allv = [v for c in curves.values() for _, v in c]
    lo, hi = min(allv), max(allv)
    lo = min(lo, 1.0)
    hi = max(hi, 1.0)
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    ticks = nice_ticks(lo, hi, 5)

    def px(d: str) -> float:
        return _scale((date.fromisoformat(d) - d0).days, 0, span, x0, x1)

    def py(v: float) -> float:
        return _scale(v, lo, hi, y1, y0)

    parts = ['<svg viewBox="0 0 %d %d" class="chart" role="img">' % (w, h)]
    for t in ticks:
        y = py(t)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (x0, y, x1, y))
        parts.append('<text x="%d" y="%.1f" class="ylab">%.2f</text>' % (x0 - 8, y + 4, t))
    cur = date(d0.year, d0.month, 1)
    while cur <= d1:
        if cur >= d0:
            x = px(cur.isoformat())
            parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="tick"/>' % (x, y1, x, y1 + 4))
            parts.append('<text x="%.1f" y="%d" class="xlab">%s</text>' % (x, y1 + 18, cur.strftime("%b")))
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="base"/>' % (x0, py(1.0), x1, py(1.0)))
    labels = []
    for i, (name, c) in enumerate(curves.items()):
        col = PALETTE[i % len(PALETTE)]
        pts = " ".join("%.1f,%.1f" % (px(d), py(v)) for d, v in c)
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>' % (pts, col))
        labels.append([py(c[-1][1]), name, col, c[-1][1]])
    labels.sort()
    used = []
    for y, name, col, v in labels:
        yl = y
        for u in used:
            if abs(yl - u) < 12:
                yl = u + 12
        used.append(yl)
        parts.append('<text x="%d" y="%.1f" class="slab" fill="%s">%s %.2fx</text>'
                     % (x1 + 6, yl + 4, col, SHORT.get(name, name), v))
    parts.append("</svg>")
    return "".join(parts)


def svg_money(curve: list, w: int = 700, h: int = 260) -> str:
    pad_l, pad_r, pad_t, pad_b = 52, 16, 14, 26
    x0, x1 = pad_l, w - pad_r
    y0, y1 = pad_t, h - pad_b
    d0 = date.fromisoformat(curve[0][0])
    d1 = date.fromisoformat(curve[-1][0])
    span = max(1, (d1 - d0).days)
    vals = [v for _, v in curve]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.10
    lo, hi = lo - pad, hi + pad
    ticks = nice_ticks(lo, hi, 4)

    def px(d: str) -> float:
        return _scale((date.fromisoformat(d) - d0).days, 0, span, x0, x1)

    def py(v: float) -> float:
        return _scale(v, lo, hi, y1, y0)

    parts = ['<svg viewBox="0 0 %d %d" class="chart" role="img">' % (w, h)]
    for t in ticks:
        y = py(t)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (x0, y, x1, y))
        parts.append('<text x="%d" y="%.1f" class="ylab">%s%.1fk</text>'
                     % (x0 - 8, y + 4, chr(36), t / 1000.0))
    cur = date(d0.year, d0.month, 1)
    while cur <= d1:
        if cur >= d0:
            x = px(cur.isoformat())
            parts.append('<text x="%.1f" y="%d" class="xlab">%s</text>' % (x, y1 + 18, cur.strftime("%b")))
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    pts = " ".join("%.1f,%.1f" % (px(d), py(v)) for d, v in curve)
    area = "%.1f,%.1f " % (x0, py(vals[0])) + pts + " %.1f,%.1f" % (x1, py(vals[0]))
    parts.append('<polygon points="%s" class="area"/>' % area)
    parts.append('<polyline points="%s" fill="none" stroke="#2f6f4f" stroke-width="2"/>' % pts)
    parts.append('<text x="%d" y="%d" class="slab" fill="#2f6f4f" text-anchor="end">%s%s</text>'
                 % (x1, y0 + 4, chr(36), format(vals[-1], ",.0f")))
    parts.append("</svg>")
    return "".join(parts)


def svg_months(months: list, w: int = 700, h: int = 180) -> str:
    pad_l, pad_r, pad_t, pad_b = 46, 10, 12, 30
    x0, x1 = pad_l, w - pad_r
    y0, y1 = pad_t, h - pad_b
    vals = [v for _, v in months]
    lo = min(min(vals), 0.0)
    hi = max(max(vals), 0.0)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.12, hi + span * 0.12

    def py(v: float) -> float:
        return _scale(v, lo, hi, y1, y0)

    step = (x1 - x0) / max(1, len(months))
    bw = step * 0.58
    parts = ['<svg viewBox="0 0 %d %d" class="chart" role="img">' % (w, h)]
    for t in nice_ticks(lo, hi, 4):
        y = py(t)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>' % (x0, y, x1, y))
        parts.append('<text x="%d" y="%.1f" class="ylab">%.0f%%</text>' % (x0 - 8, y + 4, t * 100))
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="base"/>' % (x0, py(0), x1, py(0)))
    for i, (lab, v) in enumerate(months):
        cx = x0 + step * (i + 0.5)
        yv = py(v)
        yz = py(0)
        top = min(yv, yz)
        hh = abs(yv - yz)
        col = "#2f6f4f" if v >= 0 else "#a2413a"
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity="0.85" rx="2"/>'
                     % (cx - bw / 2, top, bw, max(1.0, hh), col))
        parts.append('<text x="%.1f" y="%d" class="xlab" text-anchor="middle">%s</text>'
                     % (cx, y1 + 18, lab))
    parts.append("</svg>")
    return "".join(parts)


CSS = """
:root{
 --bg:#ffffff; --fg:#1b1c1a; --muted:#6b6f69; --line:#e3e0d8; --card:#faf9f5;
 --green:#2f6f4f; --amber:#b5762a; --blue:#3f6f9a; --plum:#8a4f7d; --grey:#6f6f6f;
 --red:#a2413a;
}
@media (prefers-color-scheme: dark){
 :root{ --bg:#141613; --fg:#e8e6df; --muted:#9aa096; --line:#2c302b; --card:#1b1e1a;
        --green:#7fbb98; --amber:#d9a35c; --blue:#8fb4d6; --plum:#c496b7; --red:#d98b83; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:840px;margin:0 auto;padding:26px 18px 60px}
h1{font-size:26px;line-height:1.2;margin:0 0 6px;letter-spacing:-0.01em}
h2{font-size:17px;margin:34px 0 10px;letter-spacing:-0.01em}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 22px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:0 0 8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 13px}
.kpi .v{font-size:22px;font-weight:600;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
.kpi .k{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
th,td{padding:5px 4px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:var(--muted);font-weight:600}
tbody tr:hover{background:var(--card)}
td.l{text-align:left}
.pos{color:var(--green)} .neg{color:var(--red)}
.rule{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--muted)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.chart{width:100%;height:auto;display:block;margin:6px 0 2px}
.grid{stroke:var(--line);stroke-width:1}
.base{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;opacity:.6}
.tick{stroke:var(--muted);stroke-width:1;opacity:.5}
.ylab{fill:var(--muted);font-size:10px;text-anchor:end}
.xlab{fill:var(--muted);font-size:10px;text-anchor:middle}
.slab{font-size:11px;font-weight:600}
.area{fill:var(--green);opacity:.10}
.corr td{text-align:center;font-size:12px}
.corr th{text-align:center}
ul{padding-left:18px;margin:8px 0}
li{margin:6px 0}
.note{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-size:13px;color:var(--muted)}
.note b{color:var(--fg)}
"""


def table_main(picks: pd.DataFrame) -> str:
    head = ("<tr><th>Coin</th><th>Rule</th><th>Trades</th><th>Win (95% CI)</th>"
            "<th>PF</th><th>Exp R</th><th>Net</th><th>Max DD</th><th>Sharpe</th>"
            "<th>Sortino</th><th>Calmar</th><th>In mkt</th></tr>")
    rows = []
    for _, r in picks.sort_values("ret", ascending=False).iterrows():
        rows.append(
            "<tr><td class=l><b>%s</b></td><td class=l><span class=rule>%s</span></td>"
            "<td>%d</td><td>%s <span class=rule>%s\u2013%s</span></td><td>%s</td>"
            "<td>%s</td><td class=pos>%s</td><td class=neg>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                SHORT.get(r.symbol, r.symbol), r.label, int(r.trades),
                pc(r.win), pc(r.win_lo, 0, False), pc(r.win_hi, 0, False),
                num(r.pf), num(r.exp_r), pc(r.ret), pc(r.dd), num(r.sharpe),
                num(r.sortino), num(r.calmar), pc(r.expo, 0, False)))
    return "<div class=scroll><table>%s%s</table></div>" % (head, "".join(rows))


def table_prob(picks: pd.DataFrame) -> str:
    head = ("<tr><th>Coin</th><th>P(year &gt; 0)</th><th>Bootstrap median</th>"
            "<th>5th pct</th><th>Median DD</th><th>5th pct DD</th>"
            "<th>Neighbours</th><th>H1</th><th>H2</th><th>Loss streak</th></tr>")
    rows = []
    for _, r in picks.sort_values("ret", ascending=False).iterrows():
        rows.append(
            "<tr><td class=l><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>" % (
                SHORT.get(r.symbol, r.symbol), pc(r.p_pos, 0, False), pc(r.boot_med),
                pc(r.boot_p05), pc(r.boot_med_dd), pc(r.boot_p05_dd),
                pc(r.nb_med), pc(r.h1_ret), pc(r.h2_ret), int(r.max_cl)))
    return "<div class=scroll><table>%s%s</table></div>" % (head, "".join(rows))


def table_corr(corr: dict) -> str:
    names = list(corr.keys())
    head = "<tr><th></th>%s</tr>" % "".join("<th>%s</th>" % SHORT.get(n, n) for n in names)
    rows = []
    for a in names:
        cells = []
        for c in names:
            v = corr[a][c]
            if a == c:
                cells.append("<td style='color:var(--muted)'>100</td>")
                continue
            alpha = min(0.55, abs(v) * 1.6)
            bg = "rgba(47,111,79,%0.2f)" % alpha if v >= 0 else "rgba(162,65,58,%0.2f)" % alpha
            cells.append("<td style='background:%s'>%d</td>" % (bg, round(v * 100)))
        rows.append("<tr><th class=l style='text-align:left'>%s</th>%s</tr>"
                    % (SHORT.get(a, a), "".join(cells)))
    return "<div class=scroll><table class=corr>%s%s</table></div>" % (head, "".join(rows))


def main() -> None:
    picks = pd.read_csv(HERE / "five_sleeves_picks.csv")
    book = json.loads((HERE / "five_sleeves_book.json").read_text(encoding="utf-8"))
    b = book["book"]
    curves = {k: v for k, v in book["curves"].items()}
    win0, win1 = book["window"]

    best = picks.sort_values("ret", ascending=False).iloc[0]
    total_cells = int(picks["cells"].sum())
    split_cells = int(picks["cells_ok"].sum())

    says = []
    for sym in picks.sort_values("ret", ascending=False)["symbol"]:
        st = next(s for s in book["sleeves"] if s["symbol"] == sym)
        p = picks[picks.symbol == sym].iloc[0]
        k = st["stats"]
        frag = ("<li><b>%s</b> — %s. %d trades, %s win rate, %.2f profit factor, "
                "%s net against a %s worst drawdown. Held the coin %s of the year; "
                "expectancy %.2fR per trade." % (
                    SHORT[sym], st["label"], k["trades"], pc(k["win"]), k["pf"],
                    pc(k["ret"]), pc(k["dd"]), pc(k["expo"], 0, False), k["exp_r"]))
        if p.nb_med < p.ret * 0.45:
            frag += (" <span class=neg>Fragile: the cells one step away in the grid "
                     "median %s, so most of this is one corner of the surface.</span>" % pc(p.nb_med))
        says.append(frag + "</li>")

    html = []
    html.append("<!doctype html><html lang=en><head><meta charset=utf-8>")
    html.append("<meta name=viewport content='width=device-width,initial-scale=1'>")
    html.append("<title>Five sleeves, tuned per coin</title>")
    html.append("<style>" + CSS + "</style></head><body><div class=wrap>")
    html.append("<h1>Five sleeves, one per coin — tuned on that coin</h1>")
    html.append("<p class=sub>Binance spot, 4h bars, %s → %s. Signal on the close, fill at the "
                "next open, 10 bps fee + 5 bps slippage a side, 3%% of equity at risk, "
                "$1,000 a wallet, long only. %d grid cells run, %d of them with 12+ trades; "
                "the pick per coin is the best full-year Calmar inside that set.</p>"
                % (win0, win1, total_cells, split_cells))

    html.append("<div class=kpis>")
    for k, v, cls in [("Book return", pc(b["ret"]), "pos"), ("Max drawdown", pc(b["dd"]), "neg"),
                      ("Sharpe", num(b["sharpe"]), ""), ("Sortino", num(b["sortino"]), ""),
                      ("Calmar", num(b["calmar"]), ""), ("Positive months", pc(b["m_pos"], 0, False), "")]:
        html.append("<div class=kpi><div class='v %s'>%s</div><div class=k>%s</div></div>"
                    % (cls, v, k))
    html.append("</div>")
    html.append("<p class=sub>Five $1,000 wallets run side by side and summed: $5,000 → %s, "
                "worst month %s, annualised vol %s. Daily-return correlation between "
                "sleeves is 0.18–0.32, and the JUP sleeve is uncorrelated with the rest "
                "(≈0), which is why the book's drawdown is a third of the worst sleeve's.</p>"
                % (format(b["end"], ",.0f"), pc(b["m_worst"]), pc(b["vol"], 0, False)))

    html.append("<h2>The five, standard analysis</h2>")
    html.append(table_main(picks))

    html.append("<h2>Equity, each wallet normalised to 1.0</h2>")
    html.append(svg_multi(curves))
    html.append("<p class=sub>The straight line is what a plateau looks like; the kinks are "
                "where a coin's trend stopped paying. ARB made almost nothing in the first "
                "half (+10.1%%) and then doubled down in the second (+51.8%%); NEAR did the "
                "reverse. Neither ordering was knowable in advance.</p>")

    html.append("<h2>The book, $5,000 combined</h2>")
    html.append(svg_money(book["book_curve"]))
    html.append("<p class=sub>Monthly returns, five wallets summed.</p>")
    html.append(svg_months(book["months"]))

    html.append("<h2>Robustness, not just the headline</h2>")
    html.append("<p class=sub>Full-year Calmar is fitted on the reported year, so each number "
                "above is paired with tests that can disappoint it. P(year &gt; 0) is a "
                "5,000-draw bootstrap of the same trades in random order at 3%% risk; "
                "neighbours are the cells one grid step away.</p>")
    html.append(table_prob(picks))

    html.append("<h2>What each sleeve actually did</h2><ul>%s</ul>" % "".join(says))

    html.append("<h2>Correlation of daily returns</h2>")
    html.append(table_corr(book["corr"]))

    html.append("<h2>The honest caveats</h2>")
    html.append("<div class=note>"
                "<p><b>This is the best of %d tests, not a forecast.</b> Per coin the grid ran "
                "%d cells; taking the top Calmar out of a few hundred inflates it. The two "
                "checks that matter are both in the table: PYTH's neighbours median %s "
                "(most of the surface makes a third of the pick) and JUP's %s "
                "(a spike surrounded by cells that make single digits), so those two are the "
                "weakest claims here even though JUP's headline is the second best.</p>"
                "<p><b>Sample size.</b> 30–79 trades a year is enough to rank a rule, not enough "
                "to trust a Sharpe above 2. ARB's 2.60 comes from 30 trades; one trade is "
                "3.3%% of the sample. The 95%% Wilson intervals in the table are the honest "
                "width: ARB is 39–73%% win rate, not 57%%.</p>"
                "<p><b>Selection on one slice.</b> The repo rule is to tune on the earlier half "
                "and report the later one; doing that on a single coin gives 20–40 trades a half, "
                "which is why the earlier per-symbol study found the fitted config loses out of "
                "sample. These five are therefore <i>lab picks with full-year numbers attached</i>, "
                "not deployable sleeves. The halved columns (H1/H2) are the closest thing to a "
                "holdout that exists at this sample size, and they are not independent.</p>"
                "<p><b>Costs and fills.</b> 15 bps round trip is realistic for these pairs at "
                "$1,000; at $10,000 the thinner ones (PYTH, JUP, RENDER) will start to slip more "
                "than modelled. Stops are assumed to fill at the stop price; gap-throughs are "
                "priced at the bar's open, and both-hit bars are scored as a loss.</p>"
                "<p><b>Window.</b> %s → %s is one regime. Every sleeve is long-only spot, so a "
                "year that grinds down ends flat at best, and the 3%% risk sizing means the "
                "returns above scale linearly with risk — and so does the drawdown.</p>"
                "</div>" % (total_cells, int(picks.cells.max()),
                            pc(picks[picks.symbol == "PYTH/USDT"].nb_med.iloc[0]),
                            pc(picks[picks.symbol == "JUP/USDT"].nb_med.iloc[0]),
                            win0, win1))

    html.append("<h2>Verdict</h2>")
    html.append("<ul>"
                "<li>Four of the five hold up: return well above their drawdown, a win rate "
                "whose confidence interval sits above break-even, and a neighbourhood that also "
                "makes money. NEAR, RENDER and ARB are the three I would carry forward.</li>"
                "<li>PYTH is the weak one — the rule needs a 3R target to pay at all, wins only "
                "38%% of the time, and the surface around it is thin. It adds %s of the book's "
                "%s points while using the most exposure (%s of the year).</li>"
                "<li>JUP's sleeve is the cleanest shape (63%% wins, 1.5R target, −7.9%% dd, zero "
                "correlation) but the grid says it sits on a spike: neighbours median %s. Treat "
                "the +58.8%% as the top of the range, not the middle.</li>"
                "<li>As a book the five combine into +%s with a −%s drawdown, because the "
                "sleeves trade different coins at different times. That is the real argument for "
                "one rule per coin over one rule for all coins.</li>"
                "<li>Next step that would actually settle it: run these five forward on paper "
                "for a quarter and compare trade-by-trade against this table, rather than "
                "tuning them again on the same year.</li>"
                "</ul>" % (pc(picks[picks.symbol == "PYTH/USDT"].ret.iloc[0] / 5),
                           pc(b["ret"], 1, False),
                           pc(picks[picks.symbol == "PYTH/USDT"].expo.iloc[0], 0, False),
                           pc(picks[picks.symbol == "JUP/USDT"].nb_med.iloc[0]),
                           pc(b["ret"], 1, False), pc(abs(b["dd"]), 1, False)))
    html.append("</div></body></html>")

    out = HERE / "five_sleeves.html"
    out.write_text("".join(html), encoding="utf-8")
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
