"""Performance statistics computed from an equity curve and a trade list."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .backtest import BacktestResult

METRIC_LABELS: dict[str, str] = {
    "total_return": "Total return",
    "buy_and_hold": "Buy & hold",
    "cagr": "CAGR",
    "sharpe": "Sharpe (annualised)",
    "sortino": "Sortino (annualised)",
    "max_drawdown": "Max drawdown",
    "calmar": "Calmar",
    "trades": "Round trips",
    "win_rate": "Win rate",
    "profit_factor": "Profit factor",
    "avg_trade": "Avg trade return",
    "best_trade": "Best trade",
    "worst_trade": "Worst trade",
    "exposure": "Avg exposure",
    "fees": "Fees paid",
}

_PCT = {"total_return", "buy_and_hold", "cagr", "max_drawdown", "win_rate", "avg_trade", "best_trade", "worst_trade", "exposure"}


def _safe_div(a: float, b: float) -> float:
    return a / b if b else float("nan")


def infer_bars_per_year(timestamps) -> float:
    """Estimate bars/year from the median spacing (e.g. 8760 for hourly bars)."""
    if len(timestamps) < 2:
        return 365.0
    gaps = pd.Series(timestamps).diff().dropna().dt.total_seconds()
    median = float(gaps.median()) if len(gaps) else 0.0
    if median <= 0:
        return 365.0
    return max(1.0, round(365.25 * 24 * 3600 / median))


def compute_metrics(result: BacktestResult, bars_per_year: float = 365.0) -> dict[str, float]:
    equity = result.equity_curve.astype(float)
    if equity.empty:
        return {}

    returns = equity.pct_change().dropna()
    initial = float(result.initial_equity)
    final = float(equity.iloc[-1])
    total_return = _safe_div(final, initial) - 1.0 if initial else float("nan")

    bars = len(equity)
    years = bars / bars_per_year if bars_per_year > 0 else float("nan")
    growth = final / initial if initial > 0 else float("nan")
    cagr = growth ** (1.0 / years) - 1.0 if years and years > 0 and growth > 0 else float("nan")

    mean_r = float(returns.mean()) if len(returns) else 0.0
    std_r = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = mean_r / std_r * math.sqrt(bars_per_year) if std_r > 0 else float("nan")

    downside = returns[returns < 0]
    down_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = mean_r / down_std * math.sqrt(bars_per_year) if down_std > 0 else float("nan")

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd = float(drawdown.min())
    calmar = _safe_div(cagr, abs(max_dd)) if max_dd < 0 and not math.isnan(cagr) else float("nan")

    pnls = np.array([t.return_pct for t in result.trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))

    close = result.close.astype(float)
    buy_hold = float(close.iloc[-1] / close.iloc[0] - 1.0) if len(close) else float("nan")

    return {
        "total_return": total_return,
        "buy_and_hold": buy_hold,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "trades": float(len(pnls)),
        "win_rate": _safe_div(len(wins), len(pnls)) if len(pnls) else float("nan"),
        "profit_factor": _safe_div(gross_win, gross_loss),
        "avg_trade": float(pnls.mean()) if len(pnls) else float("nan"),
        "best_trade": float(pnls.max()) if len(pnls) else float("nan"),
        "worst_trade": float(pnls.min()) if len(pnls) else float("nan"),
        "exposure": float(result.exposure.mean()),
        "fees": float(sum(f.fee for f in result.fills)),
    }


def format_metrics(metrics: dict[str, float]) -> str:
    """Render metrics as an aligned two-column text block."""
    rows: list[tuple[str, str]] = []
    for key, label in METRIC_LABELS.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if math.isnan(value):
            text = "n/a"
        elif key in _PCT:
            text = f"{value * 100:.2f}%"
        elif key in {"sharpe", "sortino", "calmar", "profit_factor"}:
            text = f"{value:.2f}"
        elif key == "fees":
            text = f"{value:,.2f}"
        else:
            text = f"{value:,.0f}"
        rows.append((label, text))
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"  {label.ljust(width)}  {text:>12}" for label, text in rows)
