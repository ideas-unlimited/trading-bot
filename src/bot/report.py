"""Human-readable output: summary text, trade log csv and an equity chart."""

from __future__ import annotations

import csv
from pathlib import Path

from .backtest import BacktestResult
from .metrics import format_metrics


def summary_text(result: BacktestResult, show_trades: int = 5) -> str:
    start = result.equity_curve.index[0]
    end = result.equity_curve.index[-1]
    lines = [
        f"Strategy        {result.strategy}",
        f"Period          {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M}  ({len(result.equity_curve):,} bars)",
        f"Start equity    {result.initial_equity:,.2f}",
        f"End equity      {result.final_equity:,.2f}",
        "",
        format_metrics(result.metrics),
    ]
    if result.halted_reason:
        lines += ["", f"!! trading halted: {result.halted_reason}"]

    if result.trades and show_trades:
        lines += ["", f"Last {min(show_trades, len(result.trades))} trades:"]
        for t in result.trades[-show_trades:]:
            lines.append(
                f"  {t.entry_ts:%Y-%m-%d %H:%M} -> {t.exit_ts:%Y-%m-%d %H:%M}  "
                f"{t.qty:>10.6f} @ {t.entry_price:>10.4f} -> {t.exit_price:>10.4f}  "
                f"pnl {t.pnl:>10.2f} ({t.return_pct * 100:>6.2f}%)  [{t.reason_close}]"
            )
    return "\n".join(lines)


def save_trades_csv(result: BacktestResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "fees", "pnl", "return_pct",
             "reason_open", "reason_close"]
        )
        for t in result.trades:
            writer.writerow(
                [t.entry_ts.isoformat(), t.exit_ts.isoformat(), f"{t.qty:.8f}", f"{t.entry_price:.8f}",
                 f"{t.exit_price:.8f}", f"{t.fees:.8f}", f"{t.pnl:.6f}", f"{t.return_pct:.6f}",
                 t.reason_open, t.reason_close]
            )
    return path


def save_equity_chart(result: BacktestResult, path: str | Path) -> Path:
    """Plot equity vs buy-and-hold, plus drawdown.  Requires matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    equity = result.equity_curve
    close = result.close
    norm_equity = equity / float(equity.iloc[0])
    norm_hold = close / float(close.iloc[0])
    drawdown = equity / equity.cummax() - 1.0

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ax1.plot(equity.index, norm_equity, label=f"{result.strategy}", linewidth=1.4)
    ax1.plot(close.index, norm_hold, label="buy & hold", linewidth=1.0, alpha=0.7, linestyle="--")
    ax1.set_ylabel("growth of 1.0")
    ax1.set_title(f"{result.strategy} - equity curve")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.25)

    ax2.fill_between(drawdown.index, drawdown.to_numpy() * 100, 0, color="#c0392b", alpha=0.4)
    ax2.set_ylabel("drawdown %")
    ax2.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
