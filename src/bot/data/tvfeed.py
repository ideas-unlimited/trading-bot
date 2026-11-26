"""TradingView market data (unofficial ``tvDatafeed`` library).

TradingView has no official public data API, so this adapter leans on the
community ``tvDatafeed`` package (``pip install tradingview-datafeed``).  It
works anonymously for most symbols; pass TradingView credentials via the
``TV_USERNAME`` / ``TV_PASSWORD`` environment variables for full access.

Like everything else in this project this module only *reads* data.  It can
never place an order.
"""

from __future__ import annotations

import os
from datetime import timezone
from typing import Any

from .feed import Bar

#: CLI interval aliases -> tvDatafeed ``Interval`` attribute names.
INTERVAL_ALIASES: dict[str, str] = {
    "1": "in_1_minute", "1m": "in_1_minute",
    "3m": "in_3_minutes",
    "5": "in_5_minutes", "5m": "in_5_minutes",
    "15m": "in_15_minutes",
    "30m": "in_30_minutes",
    "45m": "in_45_minutes",
    "60": "in_1_hour", "60m": "in_1_hour", "1h": "in_1_hour",
    "2h": "in_2_hours",
    "3h": "in_3_hours",
    "4h": "in_4_hours",
    "1d": "in_daily", "d": "in_daily",
    "1w": "in_weekly", "w": "in_weekly",
    "1M": "in_monthly", "M": "in_monthly",
}


def interval_attr(interval: str) -> str:
    """Resolve a CLI interval alias to a ``tvDatafeed.Interval`` attribute."""
    key = interval.strip()
    if key not in INTERVAL_ALIASES:
        valid = ", ".join(sorted(INTERVAL_ALIASES))
        raise ValueError(f"unknown TradingView interval {interval!r}; expected one of: {valid}")
    return INTERVAL_ALIASES[key]


def frame_to_bars(df: Any) -> list[Bar]:
    """Convert a tvDatafeed OHLCV dataframe into ``Bar``s.

    The dataframe has a ``datetime`` index and columns ``open, high, low,
    close, volume``.  Timestamps arrive timezone-naive and are treated as UTC.
    Rows with missing prices (common on the last, still-forming bar) are dropped.
    """
    if df is None or len(df) == 0:
        return []
    prices = df.dropna(subset=["open", "high", "low", "close"])
    bars = [
        Bar(
            ts=(ts if ts.tzinfo is not None else ts.tz_localize(timezone.utc)).astimezone(timezone.utc),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
        )
        for ts, row in prices.iterrows()
    ]
    bars.sort(key=lambda b: b.ts)
    return bars


def fetch_tv_bars(
    symbol: str,
    exchange: str,
    interval: str = "1d",
    n_bars: int = 5000,
    *,
    username: str | None = None,
    password: str | None = None,
) -> list[Bar]:
    """Download the most recent ``n_bars`` of real OHLCV data from TradingView."""
    try:
        from tvDatafeed import TvDatafeed, Interval
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "tvDatafeed is not installed. Install it with:\n"
            "  pip install tradingview-datafeed"
        ) from exc

    iv = getattr(Interval, interval_attr(interval))
    username = username or os.environ.get("TV_USERNAME") or None
    password = password or os.environ.get("TV_PASSWORD") or None
    tv = TvDatafeed(username, password) if username else TvDatafeed()
    df = tv.get_hist(symbol=symbol, exchange=exchange, interval=iv, n_bars=n_bars)
    if df is None or df.empty:
        raise RuntimeError(
            f"TradingView returned no data for {exchange}:{symbol} @ {interval}. "
            "Check the symbol/exchange spelling (e.g. BINANCE:BTCUSDT, NASDAQ:SPY)."
        )
    return frame_to_bars(df)


def fetch_latest_tv_bars(
    symbol: str,
    exchange: str,
    interval: str,
    n_bars: int = 5,
) -> list[Bar]:
    """Fetch just the most recent bars (used by ``tv-bars --watch``)."""
    return fetch_tv_bars(symbol, exchange, interval, n_bars)
