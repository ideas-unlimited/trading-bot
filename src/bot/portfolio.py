
"""Multi-coin trend rotation: the portfolio strategy the research supports.

Rule (all on completed bars, fills at the next open):

* regime  - the market proxy (BTC by default) is above its 200-bar average;
* trend   - the coin is above its price from lookback bars ago;
* rank    - among trend coins, hold the top N by lookback return;
* size    - each pick gets 1/N of capital, scaled down so its 30-bar
            realised volatility does not exceed the vol target;
* rebalance weekly, everything else stays in cash.

Turnover is low (weekly), so fees barely matter: 10 bps costs about 1 point
of CAGR in the 2018-2026 sample.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .data.feed import Bar


@dataclass(frozen=True)
class RotationConfig:
    """Parameters of the rotation.  Defaults are the researched values."""

    lookback: int = 60          # bars of momentum
    top_n: int = 2              # concurrent positions
    regime_ma: int = 200        # market-regime moving average, in bars
    vol_target: float = 0.40    # annualised volatility ceiling per position
    vol_window: int = 30
    rebalance_days: int = 7     # bars between rebalances (7 daily bars = weekly)
    initial_cash: float = 1_000.0
    fee_bps: float = 0.0
    regime_symbol: str | None = None  # None -> first symbol

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be >= 1")
        if self.lookback < 2:
            raise ValueError("lookback must be >= 2")
        if not 0 < self.vol_target <= 2:
            raise ValueError("vol_target must be in (0, 2]")
        if self.rebalance_days < 1:
            raise ValueError("rebalance_days must be >= 1")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")


@dataclass
class RotationResult:
    equity: pd.Series
    weights: pd.DataFrame       # weights actually held each bar
    target: pd.DataFrame        # target weights decided at each rebalance
    returns: pd.Series
    trades: pd.DataFrame
    yearly: pd.Series
    config: RotationConfig
    symbols: list[str] = field(default_factory=list)

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.config.initial_cash

    @property
    def current_weights(self) -> pd.Series:
        if not len(self.weights):
            return pd.Series(dtype=float)
        return self.weights.iloc[-1][self.weights.iloc[-1] > 0].sort_values(ascending=False)


def closes_from_bars(bars: Mapping[str, Sequence[Bar]]) -> pd.DataFrame:
    """Align close prices on a common timestamp index (outer join)."""
    series = {}
    for sym, rows in bars.items():
        if not rows:
            raise ValueError(f"no bars for {sym}")
        s = pd.Series({b.ts: b.close for b in rows}).sort_index()
        series[sym] = s[~s.index.duplicated(keep="last")]
    frame = pd.DataFrame(series).sort_index()
    return frame


def load_closes_from_csv(directory: str | Path, symbols: Iterable[str],
                         timeframe: str = "1d") -> pd.DataFrame:
    """Load {SYMBOL}_{timeframe}.csv files written by the Binance fetcher."""
    directory = Path(directory)
    bars: dict[str, list[Bar]] = {}
    for sym in symbols:
        path = directory / f"{sym}_{timeframe}.csv"
        if not path.exists():
            path = directory / f"{sym}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found")
        from .data.feed import load_csv

        bars[sym] = load_csv(path)
    return closes_from_bars(bars)


def target_weights(closes: pd.DataFrame, config: RotationConfig) -> pd.DataFrame:
    """Weights decided at each bar's close (callers must shift before use)."""
    rets = closes.pct_change()
    mom = closes / closes.shift(config.lookback) - 1.0
    trend = mom > 0
    regime_sym = config.regime_symbol or str(closes.columns[0])
    regime = closes[regime_sym] > closes[regime_sym].rolling(config.regime_ma).mean()
    eligible = trend.mul(regime, axis=0).fillna(False)

    ann = float(max(len(closes) / max((closes.index[-1] - closes.index[0]).days / 365.25, 1e-9), 1.0))
    vol = rets.rolling(config.vol_window).std() * math.sqrt(ann)

    rank = mom.where(eligible).rank(axis=1, ascending=False)
    picked = (rank <= config.top_n).fillna(False)
    slot = 1.0 / config.top_n
    w = picked.astype(float) * slot
    scale = (config.vol_target / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0)
    w = (w * scale.fillna(0.0)).clip(upper=1.0)
    # A coin that is not picked must never retain weight.
    return w.where(picked, 0.0).fillna(0.0)


def simulate_rotation(closes: pd.DataFrame, config: RotationConfig | None = None) -> RotationResult:
    config = config or RotationConfig()
    closes = closes.sort_index()
    returns = closes.pct_change().fillna(0.0)
    target = target_weights(closes, config)

    # Rebalance on the first bar and every rebalance_days thereafter; the
    # weights are applied from the NEXT bar (no lookahead).
    reb = target.iloc[:: config.rebalance_days]
    held = reb.reindex(closes.index).ffill().shift(1).fillna(0.0)

    gross = (held * returns).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    cost = turnover * (config.fee_bps / 10_000.0)
    net = gross - cost
    equity = (1.0 + net).cumprod() * config.initial_cash

    trades = _round_trips(closes, held)
    yearly = (1.0 + net).groupby(net.index.year).prod() - 1.0
    return RotationResult(
        equity=equity, weights=held, target=target, returns=net,
        trades=trades, yearly=yearly, config=config, symbols=list(closes.columns),
    )


def _round_trips(closes: pd.DataFrame, held: pd.DataFrame) -> pd.DataFrame:
    """One row per continuous holding period, per coin."""
    rows: list[dict[str, object]] = []
    for sym in closes.columns:
        w = held[sym].fillna(0.0)
        price = closes[sym]
        on = w > 0
        start = None
        for ts, active in on.items():
            if active and start is None:
                start = ts
            elif not active and start is not None:
                entry, exit_ = float(price.loc[start]), float(price.loc[ts])
                rows.append({
                    "symbol": sym, "entry_ts": start, "exit_ts": ts,
                    "entry": entry, "exit": exit_,
                    "return_pct": exit_ / entry - 1.0 if entry else np.nan,
                    "max_weight": float(w.loc[start:ts].max()),
                })
                start = None
        if start is not None:
            entry, exit_ = float(price.loc[start]), float(price.iloc[-1])
            rows.append({
                "symbol": sym, "entry_ts": start, "exit_ts": price.index[-1],
                "entry": entry, "exit": exit_,
                "return_pct": exit_ / entry - 1.0 if entry else np.nan,
                "max_weight": float(w.loc[start:].max()),
            })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("entry_ts").reset_index(drop=True)
    return out


def summarize(result: RotationResult) -> dict[str, float]:
    eq = result.equity
    r = result.returns
    if len(eq) < 2:
        return {}
    years = max(len(r) / _bars_per_year(result), 1e-9)
    total = eq.iloc[-1] / result.config.initial_cash - 1.0
    cagr = (eq.iloc[-1] / result.config.initial_cash) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
    vol = float(r.std() * math.sqrt(_bars_per_year(result)))
    sharpe = float(r.mean() * _bars_per_year(result) / vol) if vol > 0 else float("nan")
    downside = r[r < 0]
    dvol = float(downside.std() * math.sqrt(_bars_per_year(result))) if len(downside) > 1 else 0.0
    sortino = float(r.mean() * _bars_per_year(result) / dvol) if dvol > 0 else float("nan")
    dd = float((eq / eq.cummax() - 1.0).min())
    trades = result.trades
    wins = trades[trades["return_pct"] > 0] if len(trades) else trades
    return {
        "total_return": total,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": dd,
        "calmar": cagr / abs(dd) if dd < 0 else float("nan"),
        "trades": float(len(trades)),
        "win_rate": float(len(wins) / len(trades)) if len(trades) else float("nan"),
        "exposure": float(result.weights.sum(axis=1).mean()),
        "final": float(eq.iloc[-1]),
        "years": years,
    }


def _bars_per_year(result: RotationResult) -> float:
    idx = result.equity.index
    if len(idx) < 2:
        return 365.0
    span_days = (idx[-1] - idx[0]).total_seconds() / 86400.0
    if span_days <= 0:
        return 365.0
    return max(1.0, (len(idx) - 1) * 365.25 / span_days)


def format_summary(result: RotationResult) -> str:
    s = summarize(result)
    if not s:
        return "no data"
    lines = [
        f"Rotation          top {result.config.top_n} of {', '.join(result.symbols)}",
        f"Period            {result.equity.index[0]:%Y-%m-%d} -> {result.equity.index[-1]:%Y-%m-%d}"
        f"  ({len(result.equity):,} bars)",
        f"Start equity      {result.config.initial_cash:,.2f}",
        f"End equity        {s['final']:,.2f}",
        "",
        f"  Total return      {s['total_return'] * 100:>10.1f}%",
        f"  CAGR              {s['cagr'] * 100:>10.1f}%",
        f"  Volatility        {s['vol'] * 100:>10.1f}%",
        f"  Sharpe            {s['sharpe']:>10.2f}",
        f"  Sortino           {s['sortino']:>10.2f}",
        f"  Max drawdown      {s['max_drawdown'] * 100:>10.1f}%",
        f"  Calmar            {s['calmar']:>10.2f}",
        f"  Round trips       {s['trades']:>10.0f}",
        f"  Win rate          {s['win_rate'] * 100:>10.1f}%",
        f"  Avg exposure      {s['exposure'] * 100:>10.1f}%",
        "",
        "Yearly returns",
    ]
    lines += [f"  {year}  {value * 100:+7.1f}%" for year, value in result.yearly.items()]
    current = result.current_weights
    lines.append("")
    if len(current):
        lines.append("Current target")
        lines += [f"  {sym:<10} {w * 100:5.1f}%  ({w * result.config.initial_cash:,.0f} on 1k)"
                  for sym, w in current.items()]
    else:
        lines.append("Current target    cash")
    return "\n".join(lines)


def save_rotation(result: RotationResult, outdir: str | Path) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {
        "equity": outdir / "equity.csv",
        "weights": outdir / "weights.csv",
        "trades": outdir / "trades.csv",
    }
    result.equity.rename("equity").to_csv(paths["equity"], index_label="ts")
    result.weights.to_csv(paths["weights"], index_label="ts")
    if len(result.trades):
        result.trades.to_csv(paths["trades"], index=False)
    else:
        paths["trades"].write_text("symbol,entry_ts,exit_ts,entry,exit,return_pct,max_weight\n",
                                   encoding="utf-8")
    return paths
