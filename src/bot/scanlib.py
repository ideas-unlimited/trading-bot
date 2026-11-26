"""Shared scan logic: backtest strategy/timeframe combinations and rank them.

Used by both the CLI (``python -m bot scan``) and the web UI (``/api/scan``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .alpaca_client import AlpacaClient
from .backtest import run_backtest
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import generate_synthetic
from .metrics import infer_bars_per_year
from .strategy import build


def load_scan_bars(
    symbol: str,
    timeframe: str,
    *,
    source: str = "alpaca",
    days: int = 365,
    start: datetime | None = None,
    n_bars: int = 10_000,
) -> list[Any]:
    """Fetch bars for one symbol/timeframe.

    ``source``: alpaca (stocks + Alpaca crypto pairs), tv (TradingView data
    for anything else, symbol given as ``EXCHANGE:TICKER``), or synthetic.
    """
    if source == "synthetic":
        return generate_synthetic(2_000, seed=11, step=timedelta(hours=1))
    if source == "tv":
        from .data.feed import aggregate_bars
        from .data.tvfeed import fetch_tv_bars

        exchange, _, sym = symbol.partition(":")
        if not sym:
            sym, exchange = exchange, "BINANCE"
        if timeframe == "6h":
            raw = fetch_tv_bars(sym, exchange, "1h", n_bars)
            return aggregate_bars(raw, 6)
        return fetch_tv_bars(sym, exchange, timeframe, n_bars)
    if start is None and days:
        start = datetime.now(timezone.utc) - timedelta(days=days)
    client = AlpacaClient()
    return client.fetch_bars(symbol, timeframe, start=start, limit=n_bars)


def make_config(bars: list[Any], **overrides: Any) -> BotConfig:
    kwargs: dict[str, Any] = {}
    if "initial_cash" in overrides and overrides["initial_cash"] is not None:
        kwargs["initial_cash"] = float(overrides["initial_cash"])
    costs = {
        k: float(overrides[k])
        for k in ("fee_bps", "slippage_bps")
        if overrides.get(k) is not None
    }
    risk = {
        k: float(overrides[k])
        for k in ("risk_per_trade", "max_position_pct", "max_drawdown_stop")
        if overrides.get(k) is not None
    }
    return BotConfig(
        bars_per_year=infer_bars_per_year([b.ts for b in bars]),
        costs=CostConfig(**costs) if costs else CostConfig(),
        risk=RiskConfig(**risk) if risk else RiskConfig(),
        **kwargs,
    )


def run_scan(
    symbols: list[str],
    timeframes: list[str],
    strategy_names: list[str],
    *,
    source: str = "alpaca",
    days: int = 365,
    start: datetime | None = None,
    n_bars: int = 10_000,
    params: dict[str, Any] | None = None,
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    """Backtest every (symbol, timeframe, strategy) combination.

    Returns rows sorted by Sharpe (NaN last).  Invalid strategy parameters
    raise ``ValueError`` from ``strategy.build``.
    """
    params = params or {}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        for tf in timeframes:
            try:
                bars = load_scan_bars(
                    symbol, tf, source=source, days=days, start=start, n_bars=n_bars
                )
            except Exception as exc:  # noqa: BLE001 - report per-combination failures
                rows.append(
                    {"symbol": symbol, "timeframe": tf, "strategy": "-", "error": str(exc)}
                )
                continue
            if len(bars) < 60:
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "strategy": "-",
                        "error": f"only {len(bars)} bars fetched",
                    }
                )
                continue
            config = make_config(bars, **config_overrides)
            for name in strategy_names:
                try:
                    strategy = build(name, **params)
                except (TypeError, ValueError) as exc:
                    rows.append(
                        {"symbol": symbol, "timeframe": tf, "strategy": name, "error": str(exc)}
                    )
                    continue
                result = run_backtest(bars, strategy, config)
                m = result.metrics
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "strategy": name,
                        "bars": len(bars),
                        "total_return": m.get("total_return"),
                        "buy_and_hold": m.get("buy_and_hold"),
                        "sharpe": m.get("sharpe"),
                        "sortino": m.get("sortino"),
                        "max_drawdown": m.get("max_drawdown"),
                        "calmar": m.get("calmar"),
                        "trades": int(m.get("trades") or 0),
                        "win_rate": m.get("win_rate"),
                        "profit_factor": m.get("profit_factor"),
                        "halted": bool(result.halted_reason),
                    }
                )

    def sort_key(row: dict[str, Any]) -> float:
        sharpe = row.get("sharpe")
        if not isinstance(sharpe, (int, float)) or sharpe != sharpe:  # NaN or missing
            return float("-inf")
        return float(sharpe)

    return sorted(rows, key=sort_key, reverse=True)
