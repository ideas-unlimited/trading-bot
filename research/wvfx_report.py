"""Build research/wvfx_report.html from the bars cache + tuning CSVs."""

from __future__ import annotations

import json
import sqlite3  # unused; kept out
import sys

import pandas as pd

sys.path.insert(0, "src")

from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.config import BotConfig, CostConfig, RiskConfig  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy import build  # noqa: E402

SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
TFS = ["4h", "6h", "1d"]
BPY = {"4h": 2190.0, "6h": 1460.0, "1d": 365.0}
IS_FRACTION = 0.60

DEFAULT = {}
TUNED = dict(oversold=20, oversold_window=5, entry_window=1, trigger="cross",
             regime_days=200, stop_atr=1.5, rr=2.5, max_hold=60, exit_cross=False)


def run(symbol: str, tf: str, params: dict) -> dict:
    bars = load_scan_bars(symbol, tf, source="binance", days=730)
    bi = int(len(bars) * IS_FRACTION)
    b_ts = bars[bi].ts
    cfg = BotConfig(initial_cash=1000.0, costs=CostConfig(),
                    risk=RiskConfig(risk_per_trade=0.03, max_position_pct=0.95),
                    bars_per_year=BPY[tf])
    eng = Engine(bars_to_frame(bars), build("wvfx_stoch", **params), cfg)
    eng.run()
    res = eng.result()
    eq = res.equity_curve
    is_eq = eq[eq.index < b_ts]
    oos_eq = pd.concat([is_eq.iloc[[-1]], eq[eq.index >= b_ts]])
    ret = lambda s: float(s.iloc[-1] / s.iloc[0] - 1.0) if len(s) > 1 else 0.0
    dd = lambda s: float((s / s.cummax() - 1.0).min())
    tr = res.trades
    return dict(
        symbol=symbol, tf=tf, bars=len(bars),
        first=str(bars[0].ts.date()), last=str(bars[-1].ts.date()),
        full=ret(eq), is_ret=ret(is_eq) if len(is_eq) > 1 else 0.0,
        oos_ret=ret(oos_eq) if len(oos_eq) > 1 else 0.0,
        dd=dd(eq), trades=len(tr),
        oos_trades=sum(1 for t in tr if t.entry_ts >= b_ts),
        win=(sum(1 for t in tr if t.return_pct > 0) / len(tr)) if tr else 0.0,
        bh=float(bars[-1].close / bars[0].close - 1.0),
        eq=eq, b_ts=b_ts, trade_list=tr,
    )


def main() -> None:
    print("running SOL x tf ...", flush=True)
    sol = {tf: run("SOL/USDT", tf, TUNED) for tf in TFS}
    print("running default 4h ...", flush=True)
    default4h = run("SOL/USDT", "4h", DEFAULT)

    print("running matrix ...", flush=True)
    cells = []
    for sym in SYMBOLS:
        row = []
        for tf in TFS:
            r = sol[tf] if sym == "SOL/USDT" else run(sym, tf, TUNED)
            row.append(round(r["oos_ret"], 4))
        cells.append(row)

    # equity series (normalised to 100), downsampled for the SVG
    def series(r, step):
        eq = r["eq"]
        v = (eq / eq.iloc[0] * 100.0)
        idx = list(range(0, len(v), step))
        if idx[-1] != len(v) - 1:
            idx.append(len(v) - 1)
        return {"ts": [v.index[i].strftime("%Y-%m-%dT%H:%M") for i in idx],
                "v": [round(float(v.iloc[i]), 2) for i in idx]}

    step = max(1, len(sol["4h"]["eq"]) // 900)
    tuned_s = series(sol["4h"], step)
    default_s = series(default4h, step)
    bars4 = load_scan_bars("SOL/USDT", "4h", source="binance", days=730)
    bh = pd.Series([b.close for b in bars4], index=[b.ts for b in bars4])
    bh = bh / bh.iloc[0] * 100.0
    bh_idx = list(range(0, len(bh), step))
    if bh_idx[-1] != len(bh) - 1:
        bh_idx.append(len(bh) - 1)
    bh_s = {"ts": [bh.index[i].strftime("%Y-%m-%dT%H:%M") for i in bh_idx],
            "v": [round(float(bh.iloc[i]), 2) for i in bh_idx]}

    bi = int(len(tuned_s["ts"]) * IS_FRACTION)
    boundary_idx = min(bi, len(tuned_s["ts"]) - 1)

    t4 = sol["4h"]
    t6 = sol["6h"]
    t1 = sol["1d"]
    d4 = default4h

    def card(tf, r, tag, verdict):
        return dict(tf=tf, bars=r["bars"], full=r["full"], oos=r["oos_ret"],
                    is_=r["is_ret"], trades=r["trades"], win=r["win"],
                    dd=r["dd"], tag=tag, verdict=verdict)

    tf_rows = [
        card("4h", t4, "ok", "fits"),
        card("6h", t6, "no", "does not fit"),
        card("1d", t1, "no", "too few trades"),
    ]

    funnel = [
        dict(label="entry filters", n=144, is_pos=111, oos_pos=19),
        dict(label="exit geometry", n=384, is_pos=192, oos_pos=150),
        dict(label="WVF / stoch params", n=108, is_pos=82, oos_pos=58),
        dict(label="trailing stop", n=48, is_pos=48, oos_pos=2),
    ]

    trades = [dict(entry=str(t.entry_ts.date()), exit=str(t.exit_ts.date()),
                   r=round(float(t.return_pct), 4),
                   why=t.reason_close.replace(" at ", " ").split(" (")[0])
              for t in t4["trade_list"]]

    data = dict(
        window={"first": t4["first"], "last": t4["last"]},
        costs="10 bp fee + 5 bp slippage per side, 3% risk/trade",
        verdict=(
            "<b>4h fits, 6h and 1d do not.</b> Tuned WVFX made "
            f"<b>+{t4['full'] * 100:.1f}%</b> on SOL over two years with a "
            f"{t4['dd'] * 100:.1f}% max drawdown while SOL itself fell "
            f"{abs(t4['bh']) * 100:.0f}% — on {t4['trades']} trades, "
            f"out of sample +{t4['oos_ret'] * 100:.1f}%. The same rules lose "
            f"{t6['oos_ret'] * 100:.1f}% out of sample on 6h and fire "
            f"{t1['trades']} times on 1d."
        ),
        tfs=tf_rows,
        sol={"series": {"tuned": tuned_s, "default": default_s, "bh": bh_s},
             "boundaryIdx": boundary_idx},
        matrix={"symbols": SYMBOLS, "tfs": TFS, "cells": cells},
        funnel=funnel,
        trades=trades,
        chartnote=(
            f"SOL 4h, {t4['first']} → {t4['last']}. Tuned: +{t4['full'] * 100:.1f}% "
            f"({t4['trades']} trades, {t4['win'] * 100:.0f}% win). House default: "
            f"+{d4['full'] * 100:.1f}% ({d4['trades']} trades). Buy &amp; hold: "
            f"{t4['bh'] * 100:.0f}%. Nearly all of the tuned gain is in the in-sample "
            "half; the out-of-sample half is close to flat."
        ),
        heatnote=(
            "The identical parameter set on every cell, split at the same date. "
            "4h is positive on 4 of 5 coins out of sample; 6h on 2 of 5; 1d barely "
            "trades at all. Five correlated majors in one crash are not five "
            "independent proofs."
        ),
        funnelnote=(
            "Each row shows how many of that stage's configurations were positive "
            "in-sample (faded) versus out-of-sample (solid). Entry filters rarely "
            "survived (13%); exit geometry survived far more often (39%), so where "
            "the stop and target sit matters more than adding entry conditions. The "
            "trailing stop was "
            "<b>48 of 48 positive in-sample and 2 of 48 out of sample</b> — the "
            "clearest overfit in the batch. Selection on SOL alone is a coin flip."
        ),
        takeaways=[
            "<b>4h is the only timeframe that fits.</b> WVFX is a capitulation "
            "bounce that has to be out within days; at 4h it turns SOL's -70% "
            "into +25% by sitting in cash most of the time. At 6h the same "
            "rules give back the edge (-15.2% out of sample), and at 1d the "
            "setup fires 10 times across five coins in two years, which is not "
            "a strategy.",
            "<b>Tuned on the earlier slice, validated on the later one.</b> The "
            "fit saw the first 60% of the window only; the last 40% never "
            "touched a selection decision. On SOL that split reads +17.9% "
            "in-sample and +6.3% out of sample, 15 trades in total.",
            "<b>Why not SOL's own best cell.</b> Fitting SOL alone picked a "
            "shorter regime filter and scored +33.6% full / +3.8% out of "
            "sample; the config used here scores +25.3% / +6.3%, and four of "
            "the five majors prefer it with no parameter change. At ~7 trades a "
            "year a SOL-only cell is a coincidence, not an edge.",
            "<b>Trailing: tight loses, wide does not beat the target.</b> A 1.5-3.5 "
            "ATR trail on top of the target was positive 48 times in-sample and 2 "
            "times out of 48 out of sample. Dropping the target for a 5-7 ATR trail "
            "lifts the average trade (+3.2% against +1.6% out of sample, 5/5 coins) "
            "but not the account: +16.6% median against +20.5%, because the extra "
            "days in the market give back more than the runners add.",
            "<b>Still thinner than it looks.</b> 34 out-of-sample trades across "
            "five coins is a small sample: ~+1.6% equity per trade, t = 2.3 "
            "pooled, and SOL's own 10 out-of-sample trades sit inside noise. "
            "Paper it before it gets an allocation.",
            "<b>For SOL's single slot, momentum_swing 6h still looks stronger</b> "
            "(+18.9% out of sample against +6.3%, 26 trades against 15). Caveat: "
            "its parameters came from a full-window sweep, so that figure is an "
            "upper bound — WVFX's +6.3% is the clean holdout. WVFX 4h is the "
            "better fit for BTC/ETH/XRP, or a second book, not a replacement.",
        ],
        footer=(
            "Method: one causal run per config over the full window, then the equity "
            "curve and trade list are split at 60% (2026-02-09). Selection used "
            "in-sample pooled trade returns only; out-of-sample numbers are reported, "
            "never selected on. Fills at next bar open, stops checked before targets "
            "intrabar, costs charged both sides. Source: Binance spot klines cached in "
            "trading_common.bars. Scripts: research/wvfx_tune.py, wvfx_pooled.py, "
            "wvfx_xcheck.py; notes in research/WVFX_TUNE.md."
        ),
    )

    html = open("research/wvfx_report_template.html", encoding="utf-8").read()
    html = html.replace("__DATA__", json.dumps(data))
    open("research/wvfx_report.html", "w", encoding="utf-8").write(html)
    print("wrote research/wvfx_report.html", len(html), "bytes")
    for r in tf_rows:
        print(f'  {r["tf"]}: full {r["full"]:+.1%} oos {r["oos"]:+.1%} trades {r["trades"]}')
    print("boundary idx", boundary_idx, "of", len(tuned_s["ts"]), "series pts")


if __name__ == "__main__":
    main()