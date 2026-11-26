"""Alpaca Trading API client — paper trading and market data.

Uses only the standard library (``urllib``) against Alpaca's official REST
endpoints:

* trading:      ``https://paper-api.alpaca.markets``  (hard-coded paper!)
* market data:  ``https://data-api.alpaca.markets``   (stocks v2 + crypto v1beta3)

The trading base URL is deliberately hard-coded to the *paper* endpoint: this
framework's contract is that no code path can reach a real broker.  Keys come
from ``ALPACA_KEY_ID`` / ``ALPACA_SECRET_KEY`` (or a git-ignored ``.env``).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .data.feed import Bar
from .envdot import load_env

PAPER_TRADING_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"   # official Market Data API base URL

#: CLI timeframe aliases -> Alpaca ``timeframe`` strings (stocks & crypto).
TIMEFRAMES: dict[str, str] = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
    "1h": "1Hour", "4h": "4Hour",
    "1d": "1Day", "d": "1Day",
    "1w": "1Week", "w": "1Week",
    "1M": "1Month", "M": "1Month",
    "6h": "6h",  # aggregated from 1h bars client-side
}

TIMEFRAME_DURATION: dict[str, timedelta] = {
    "1Min": timedelta(minutes=1), "5Min": timedelta(minutes=5),
    "15Min": timedelta(minutes=15), "30Min": timedelta(minutes=30),
    "1Hour": timedelta(hours=1), "4Hour": timedelta(hours=4),
    "1Day": timedelta(days=1), "1Week": timedelta(weeks=1),
    "1Month": timedelta(days=30),
    "6h": timedelta(hours=6),  # aggregated from 1h bars (Alpaca has no native 6H)
}

MAX_BARS_PER_PAGE = 10_000


def resolve_timeframe(alias: str) -> str:
    key = alias.strip()
    if key not in TIMEFRAMES:
        valid = ", ".join(sorted(TIMEFRAMES))
        raise ValueError(f"unknown timeframe {alias!r}; expected one of: {valid}")
    return TIMEFRAMES[key]


def symbol_is_crypto(symbol: str) -> bool:
    """Alpaca crypto symbols look like ``BTC/USD``."""
    return "/" in symbol


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def bars_from_payload(payload: dict[str, Any]) -> list[Bar]:
    """Convert an Alpaca bars response into ``Bar``s, oldest first.

    Handles both response shapes: a list of rows, or the multi-symbol
    mapping ``{"bars": {"BTC/USD": [...]}}`` that the grouped endpoints
    return (we always request a single symbol).
    """
    rows = payload.get("bars") or []
    if isinstance(rows, dict):
        rows = next(iter(rows.values())) if rows else []
    bars = []
    for row in rows:
        if row.get("o") is None or row.get("c") is None:
            continue  # skip incomplete rows
        bars.append(
            Bar(
                ts=_parse_ts(row["t"]),
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row.get("v") or 0.0),
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars


class AlpacaError(RuntimeError):
    """An Alpaca API call failed."""


class AlpacaClient:
    """Thin authenticated REST client for Alpaca paper trading + data."""

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        *,
        env_file: str | Path = ".env",
        timeout: float = 30.0,
    ) -> None:
        load_env(env_file)
        self.key_id = key_id or os.environ.get("ALPACA_KEY_ID") or ""
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY") or ""
        if not self.key_id or not self.secret_key:
            raise AlpacaError(
                "Missing Alpaca credentials. Set ALPACA_KEY_ID and "
                "ALPACA_SECRET_KEY in .env (see .env.example) or the environment."
            )
        self.timeout = timeout

    # -- plumbing -----------------------------------------------------------
    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Content-Type": "application/json",
                "User-Agent": "trading-bot/0.1 (paper)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 404 and allow_404:
                return None
            raise AlpacaError(f"Alpaca {method} {url} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AlpacaError(f"Alpaca {method} {url} failed: {exc.reason}") from exc

    # -- market data --------------------------------------------------------
    def fetch_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10_000,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars (stocks or crypto, decided by symbol).

        ``6h`` is not a native Alpaca timeframe; it is built by aggregating
        1-hour bars (note that ``limit`` then applies to the 1h request).
        """
        if timeframe.strip() == "6h":
            from .data.feed import aggregate_bars

            raw = self._fetch_bars_raw(symbol, "1h", tf="1Hour", start=start, end=end, limit=limit)
            return aggregate_bars(raw, 6)
        return self._fetch_bars_raw(symbol, timeframe, start=start, end=end, limit=limit)

    def _fetch_bars_raw(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10_000,
        tf: str | None = None,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars; ``tf`` may pass a pre-resolved name."""
        tf = tf or resolve_timeframe(timeframe)
        params: dict[str, str] = {
            "symbols": symbol,
            "timeframe": tf,
            "limit": str(min(limit, MAX_BARS_PER_PAGE)),
        }
        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()

        if symbol_is_crypto(symbol):
            base = f"{DATA_BASE}/v1beta3/crypto/us/bars"
        else:
            base = f"{DATA_BASE}/v2/stocks/bars"

        bars: list[Bar] = []
        while True:
            payload = self._request(f"{base}?{urllib.parse.urlencode(params)}")
            bars.extend(bars_from_payload(payload))
            token = payload.get("next_page_token")
            if not token or len(bars) >= limit:
                break
            params["page_token"] = token
        return bars

    # -- paper trading ------------------------------------------------------
    def get_account(self) -> dict[str, Any]:
        return self._request(f"{PAPER_TRADING_BASE}/v2/account")

    def get_positions(self) -> list[dict[str, Any]]:
        return self._request(f"{PAPER_TRADING_BASE}/v2/positions")

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        return self._request(
            f"{PAPER_TRADING_BASE}/v2/positions/{urllib.parse.quote(symbol)}",
            allow_404=True,
        )

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        *,
        stop: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        """Submit a market order to the *paper* account.

        Stocks get a bracket order (entry + stop + optional take profit) when a
        stop is supplied.  Alpaca's crypto API does not support bracket orders,
        so crypto entries are simple market orders — the live trader manages
        crypto stops itself.
        """
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy/sell, got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        order: dict[str, Any] = {
            "symbol": symbol,
            "qty": round(qty, 8),
            "side": side,
            "type": "market",
            "time_in_force": "gtc",
        }
        if stop is not None and not symbol_is_crypto(symbol):
            order["order_class"] = "bracket"
            order["stop_loss"] = {"stop_price": round(stop, 4)}
            if take_profit is not None:
                order["take_profit"] = {"limit_price": round(take_profit, 4)}
        return self._request(
            f"{PAPER_TRADING_BASE}/v2/orders", method="POST", body=order
        )

    def close_position(self, symbol: str) -> dict[str, Any] | None:
        """Flatten a position at market (paper)."""
        return self._request(
            f"{PAPER_TRADING_BASE}/v2/positions/{urllib.parse.quote(symbol)}",
            method="DELETE",
        )
