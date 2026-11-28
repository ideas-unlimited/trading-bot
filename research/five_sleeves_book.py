"""Book view of the five tuned sleeves: five $1,000 wallets, one year, 4h.

Reads the picks the grid produced (five_sleeves_picks.csv + the cell table for
the parameters), re-runs exactly those five configurations, and reports the
combined book: return, drawdown, Sharpe, Sortino, Calmar, monthly distribution
and the daily-return correlation between the sleeves. Writes
five_sleeves_book.json for the report page.

    python research/five_sleeves_book.py
"""

from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from five_sleeves import (BPY, LOAD_DAYS, RISK, TF, WINDOW_DAYS, analyse, cfg,  # noqa: E402
                          make)
from bot.backtest import Engine, bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402


def parse_params(text: str) -> dict:
    out = {}
    for part in str(text).split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k] = ast.literal_eval(v)
        except Exception:
            out[k] = v
    return out


def run_sleeve(sym: str, kind: str, kw: dict) -> tuple[pd.Series, dict, object]:
    bars = load_scan_bars(sym, TF, source="binance", days=LOAD_DAYS)
    df = bars_to_frame(bars)
    start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
    eng = Engine(df, make(kind, kw), cfg())
    eng.run()
    res = eng.result()
    m = analyse(res, start_ts)
    eq = res.equity_curve.astype(float)
    eq = eq[eq.index >= start_ts]
    return eq, m, res


def book_stats(eq: pd.Series) -> dict:
    base = float(eq.iloc[0])
    end = float(eq.iloc[-1])
    ret = end / base - 1.0
    dd_series = eq / eq.cummax() - 1.0
    daily = eq.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    sd = float(r.std(ddof=1))
    sharpe = float(r.mean() / sd * math.sqrt(365.0)) if sd else float("nan")
    dn = float(np.sqrt(np.mean(np.minimum(r.to_numpy(), 0.0) ** 2)))
    sortino = float(r.mean() / dn * math.sqrt(365.0)) if dn else float("nan")
    month = eq.resample("MS").last()
    mret = month.pct_change().dropna()
    if len(month) and len(mret):
        mret.iloc[0] = month.iloc[0] / base - 1.0
    mid = len(eq) // 2
    return {
        "start": base, "end": end, "ret": ret, "dd": float(dd_series.min()),
        "sharpe": sharpe, "sortino": sortino,
        "calmar": ret / abs(float(dd_series.min())) if dd_series.min() < 0 else float("nan"),
        "h1": float(eq.iloc[mid] / base - 1.0), "h2": float(end / eq.iloc[mid] - 1.0),
        "months": int(len(mret)), "m_pos": float((mret > 0).mean()) if len(mret) else float("nan"),
        "m_best": float(mret.max()) if len(mret) else float("nan"),
        "m_worst": float(mret.min()) if len(mret) else float("nan"),
        "vol": float(r.std(ddof=1) * math.sqrt(365.0)),
    }


def main() -> None:
    picks = pd.read_csv(HERE / "five_sleeves_picks.csv")
    cells = pd.read_csv(HERE / "five_sleeves.csv")
    sleeves = []
    curves = {}
    rets = {}
    for _, row in picks.iterrows():
        sym, label = row["symbol"], row["label"]
        hit = cells[(cells.symbol == sym) & (cells.label == label)]
        kw = parse_params(hit.iloc[0]["params"])
        eq, m, res = run_sleeve(sym, row["kind"], kw)
        curves[sym] = eq / float(eq.iloc[0])
        rets[sym] = eq.resample("1D").last().pct_change()
        sleeves.append({"symbol": sym, "kind": row["kind"], "label": label,
                        "params": kw, "stats": m,
                        "trades": [{"entry": str(t.entry_ts), "exit": str(t.exit_ts),
                                    "ret": float(t.return_pct), "pnl": float(t.pnl),
                                    "why": t.reason_close} for t in res.trades
                                   if t.entry_ts >= eq.index[0]]})
        print(f"  {sym:12s} {label:42s} {m['ret'] * 100:+7.1f}%  "
              f"dd {m['dd'] * 100:+6.1f}%  trades {m['trades']:3d}", flush=True)

    idx = None
    for s, c in curves.items():
        idx = c.index if idx is None else idx.intersection(c.index)
    eqs = {s: c.reindex(idx).ffill() for s, c in curves.items()}
    book = sum((e * 1000.0 for e in eqs.values()))
    b = book_stats(book)
    print(f"\nBOOK  {b['ret'] * 100:+.1f}%  dd {b['dd'] * 100:+.1f}%  "
          f"sharpe {b['sharpe']:.2f}  sortino {b['sortino']:.2f}  "
          f"calmar {b['calmar']:.2f}")
    print(f"      H1 {b['h1'] * 100:+.1f}%  H2 {b['h2'] * 100:+.1f}%  "
          f"months +{b['m_pos'] * 100:.0f}%  best {b['m_best'] * 100:+.1f}%  "
          f"worst {b['m_worst'] * 100:+.1f}%  vol {b['vol'] * 100:.0f}%")

    df_rets = pd.DataFrame({k: v.reindex(pd.date_range(v.index.min(), v.index.max(), freq="1D"))
                            for k, v in rets.items()})
    corr = df_rets.corr()
    print("\ndaily-return correlation")
    print((corr * 100).round(0).astype(int).to_string())

    weekly = {}
    for s, c in curves.items():
        w = c.resample("7D").last().dropna()
        weekly[s] = [[str(t.date()), round(float(v), 4)] for t, v in w.items()]
    wb = book.resample("7D").last().dropna()
    month = book.resample("MS").last()
    mret = month.pct_change().dropna()
    if len(month):
        mret = pd.concat([pd.Series([month.iloc[0] / float(book.iloc[0]) - 1.0],
                                    index=[month.index[0]]), mret])
    months = [[str(t.strftime("%b %y")), round(float(v), 4)] for t, v in mret.items()]
    dd = (book / book.cummax() - 1.0).resample("7D").last().dropna()
    book_dd = [[str(t.date()), round(float(v), 4)] for t, v in dd.items()]
    out = {
        "window": [str(idx[0].date()), str(idx[-1].date())],
        "sleeves": sleeves,
        "book": b,
        "corr": {a: {c_: float(corr.loc[a, c_]) for c_ in corr.columns} for a in corr.index},
        "curves": weekly,
        "book_curve": [[str(t.date()), round(float(v), 4)] for t, v in wb.items()],
        "months": months,
        "book_dd": book_dd,
    }
    (HERE / "five_sleeves_book.json").write_text(json.dumps(out, indent=1, default=str),
                                                 encoding="utf-8")
    print(f"\nwrote {HERE / 'five_sleeves_book.json'}")


if __name__ == "__main__":
    main()
