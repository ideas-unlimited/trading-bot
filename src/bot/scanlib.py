"""Shared scan logic: backtest strategy/timeframe combinations and rank them.

Used by both the CLI (``python -m bot scan``) and the web UI (``/api/scan``).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from .alpaca_client import AlpacaClient
from .backtest import run_backtest
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import Bar, generate_synthetic
from .metrics import infer_bars_per_year
from .strategy import build
from . import db as store


_DB_WARNED = {"done": False}


def _warn_db_down(exc: Exception) -> None:
    """One loud line per process when the market-data DB is missing."""
    if not _DB_WARNED["done"]:
        _DB_WARNED["done"] = True
        print(f"! market-data database unavailable ({exc}); fetching directly",
              file=sys.stderr)


def cache_is_sufficient(bars: list[Any], start: Any, *, now: Any) -> bool:
    """True when the cached bars fully cover the requested range and are fresh.

    Coverage: at least 60 bars and the oldest cached bar at or before the
    requested start.  Freshness: the newest bar is within a few bar-spacings
    of now (measured from the cache's own bar spacing, so 1d stock bars
    survive weekends).
    """
    if len(bars) < 60:
        return False
    if start is not None and bars[0].ts > start:
        return False
    tail = bars[-11:]
    spans = [b.ts - a.ts for a, b in zip(tail, tail[1:]) if b.ts > a.ts]
    spacing = sorted(spans)[len(spans) // 2] if spans else None
    if spacing is None:
        return False
    return (now - bars[-1].ts) <= max(4 * spacing, spacing)


def _spacing(bars: list[Any]) -> timedelta | None:
    """Median spacing of the last bars, as a timedelta."""
    tail = bars[-11:]
    spans = [b.ts - a.ts for a, b in zip(tail, tail[1:]) if b.ts > a.ts]
    return sorted(spans)[len(spans) // 2] if spans else None


def merge_bars(cached: list[Any], fresh: list[Any]) -> list[Any]:
    """Union of two bar lists by timestamp, sorted; fresh wins on collisions."""
    seen = {b.ts: b for b in cached}
    for b in fresh:
        seen[b.ts] = b
    return [seen[k] for k in sorted(seen)]


#: how much history we keep, per user decision: 3 years is plenty for
#: regime coverage, and every backfill/prune clamps to it.
MAX_HISTORY_DAYS = 1095


def _backfill_start(now: datetime) -> datetime:
    return now - timedelta(days=MAX_HISTORY_DAYS)


def _fetch_source(
    source: str,
    symbol: str,
    timeframe: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    n_bars: int = 10_000,
) -> list[Any]:
    """Raw fetch from Alpaca or TradingView (6h aggregated from 1h on TV)."""
    if source == "binance":
        from .data.binance import fetch_binance_bars

        return fetch_binance_bars(symbol, timeframe, start=start, end=end,
                                  limit=min(n_bars, 1000))
    if source == "tv":
        from .data.feed import aggregate_bars
        from .data.tvfeed import fetch_tv_bars

        exchange, _, sym = symbol.partition(":")
        if not sym:
            sym, exchange = exchange, "BINANCE"
        tf = "1h" if timeframe == "6h" else timeframe
        bars = fetch_tv_bars(sym, exchange, tf, n_bars)
        if timeframe == "6h":
            bars = aggregate_bars(bars, 6)
        if start is not None:
            bars = [b for b in bars if b.ts >= start]
        return bars
    client = AlpacaClient()
    return client.fetch_bars(symbol, timeframe, start=start, end=end, limit=n_bars)


def load_scan_bars(
    symbol: str,
    timeframe: str,
    *,
    source: str = "alpaca",
    days: int = 365,
    start: datetime | None = None,
    n_bars: int = 10_000,
    cache_key: str | None = None,
    backfill: bool = True,
) -> list[Any]:
    """Bars for one symbol/timeframe, served from our own Postgres history DB.

    Incremental sync (delta fetching):
    - empty cache  -> one deep backfill (all history the feed allows)
    - cached bars  -> only the missing tail is fetched (last cached bar -> now)
    - deep gap     -> an extra fetch covers the older gap (Alpaca only;
                      TradingView cannot fetch by end-date)
    Fresh caches skip the API entirely.  Everything fetched is upserted
    (idempotent ON CONFLICT), so repeated research runs never re-pay the API
    cost and respect Alpaca rate limits.

    ``cache_key`` is the canonical display symbol used in the cache (e.g.
    "BNB/USDT" even when fetched as "BINANCE:BNBUSDT").  The returned bars
    are sliced to the requested window (``start`` or ``days``) so callers see
    consistent ranges regardless of how deep the cache is.
    """
    display = cache_key or symbol
    now = datetime.now(timezone.utc)
    start_eff = start if start is not None else (
        now - timedelta(days=days) if days else None
    )

    if source == "synthetic" or not store.is_available():
        if source == "synthetic":
            return generate_synthetic(2_000, seed=11, step=timedelta(hours=1))
        return _fetch_source(source, symbol, timeframe, start=start_eff, n_bars=n_bars)

    try:
        conn = store.connect_common()
    except store.DatabaseError as exc:
        _warn_db_down(exc)
        fetched = _fetch_source(source, symbol, timeframe, start=start_eff, n_bars=n_bars)
        if start_eff is not None:
            fetched = [b for b in fetched if b.ts >= start_eff]
        return fetched

    try:
        cached = store.load_bars(conn, display, timeframe)
        fresh: list[Any] = []

        if cached:
            spacing = _spacing(cached) or timedelta(hours=1)
            last = cached[-1].ts
            gap_behind = (
                start_eff is not None
                and cached[0].ts - start_eff > max(timedelta(hours=72), 4 * spacing)
            )
            is_fresh = cache_is_sufficient(cached, start_eff, now=now)

            if is_fresh and not gap_behind:
                fresh = []                      # fully served from our own DB
            else:
                if gap_behind and source == "alpaca":
                    fresh = _fetch_source(
                        source, symbol, timeframe,
                        start=start_eff, end=cached[0].ts - spacing, n_bars=n_bars,
                    ) + fresh
                if now - last > spacing:
                    fresh = fresh + _fetch_source(
                        source, symbol, timeframe,
                        start=last + spacing, n_bars=n_bars,
                    )
        else:
            # first run for this symbol/timeframe: pull all history up to
            # the 3-year window, then it's deltas from there
            deep_start = _backfill_start(now) if (backfill and source == "alpaca") else start_eff
            fresh = _fetch_source(
                source, symbol, timeframe,
                start=deep_start, n_bars=n_bars if source == "alpaca" else min(n_bars, 5_000),
            )

        if fresh:
            try:
                store.upsert_bars(conn, display, timeframe, fresh)
                store.prune_bars(conn, keep_days=MAX_HISTORY_DAYS)
            except store.DatabaseError:
                pass
        merged = merge_bars(cached, fresh)
    finally:
        conn.close()

    if start_eff is not None:
        merged = [b for b in merged if b.ts >= start_eff]
    return merged


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
