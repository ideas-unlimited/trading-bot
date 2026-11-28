"""Live order-book depth from Binance production spot.

Read-only, no API key: /api/v3/depth, /api/v3/ticker/bookTicker and
/api/v3/aggTrades against https://api.binance.com.

Binance publishes no historical order book, so depth can only be recorded
forward from the moment a recorder runs. There is no retroactive depth
backtest - unlike candles, a depth strategy can only be validated live.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

BASE_URL = "https://api.binance.com"
DEMO_BASE = "https://demo-api.binance.com"
DEPTH_PATH = "/api/v3/depth"
BOOK_TICKER_PATH = "/api/v3/ticker/bookTicker"
AGG_TRADES_PATH = "/api/v3/aggTrades"
MAX_LEVELS = 5000


class DepthError(RuntimeError):
    """Bad request or unusable payload from the depth endpoint."""


@dataclass(frozen=True)
class Level:
    price: float
    qty: float


@dataclass(frozen=True)
class BookSnapshot:
    """One order book at one instant, bids descending, asks ascending."""

    symbol: str
    ts: datetime
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    last_update_id: int = 0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        return self.asks[0].price

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.best_ask - self.best_bid) / self.mid * 10_000.0

    def imbalance(self, levels: int = 5) -> float:
        """(bid qty - ask qty) / total over the top ``levels``; -1 sells, +1 buys."""
        bid = sum(l.qty for l in self.bids[:levels])
        ask = sum(l.qty for l in self.asks[:levels])
        total = bid + ask
        return (bid - ask) / total if total else 0.0

    def notional(self, side: str, bps: float = 10.0) -> float:
        """Quote volume resting within ``bps`` of mid on one side."""
        limit = self.mid * (1 + bps / 10_000.0) if side == "ask" else self.mid * (1 - bps / 10_000.0)
        levels = self.asks if side == "ask" else self.bids
        inside = [l for l in levels if (l.price <= limit if side == "ask" else l.price >= limit)]
        return sum(l.price * l.qty for l in inside)


def normalize_symbol(symbol: str) -> str:
    """BTC/USDT -> BTCUSDT, upper case, alphanumeric only."""
    clean = symbol.strip().upper().replace("/", "").replace("-", "")
    if not clean or not clean.isascii() or not clean.isalnum():
        raise ValueError(f"not a Binance spot symbol: {symbol!r}")
    return clean


def _check_levels(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("levels must be an int")
    if not 1 <= limit <= MAX_LEVELS:
        raise ValueError(f"levels must be 1..{MAX_LEVELS}, got {limit}")
    return limit


def _default_transport(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "trading-bot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _level(raw: Any) -> Level:
    try:
        price, qty = raw[0], raw[1]
    except (TypeError, IndexError, KeyError):
        raise DepthError(f"malformed level: {raw!r}") from None
    try:
        price_f, qty_f = float(price), float(qty)
    except (TypeError, ValueError):
        raise DepthError(f"non-numeric level: {raw!r}") from None
    if price_f <= 0 or qty_f <= 0:
        raise DepthError(f"non-positive level: {raw!r}")
    return Level(price=price_f, qty=qty_f)


def parse_depth(payload: Any, symbol: str, ts: datetime | None = None) -> BookSnapshot:
    """Validate a /api/v3/depth payload and return an immutable snapshot."""
    if not isinstance(payload, dict):
        raise DepthError("depth payload is not an object")
    bids_raw, asks_raw = payload.get("bids"), payload.get("asks")
    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise DepthError("depth payload has no bids/asks arrays")
    if not bids_raw or not asks_raw:
        raise DepthError(f"empty book for {symbol}")
    bids = tuple(_level(r) for r in bids_raw)
    asks = tuple(_level(r) for r in asks_raw)
    if any(a.price <= b.price for a, b in zip(bids, bids[1:])):
        raise DepthError("bids are not strictly descending")
    if any(a.price >= b.price for a, b in zip(asks, asks[1:])):
        raise DepthError("asks are not strictly ascending")
    if bids[0].price >= asks[0].price:
        raise DepthError(f"crossed book: bid {bids[0].price} >= ask {asks[0].price}")
    update = payload.get("lastUpdateId", 0)
    return BookSnapshot(
        symbol=normalize_symbol(symbol),
        ts=(ts or datetime.now(timezone.utc)).astimezone(timezone.utc),
        bids=bids,
        asks=asks,
        last_update_id=int(update or 0),
    )


def _get(base_url: str, path: str, params: dict[str, Any], transport: Callable[[str, float], Any], timeout: float) -> Any:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return transport(f"{base_url.rstrip(chr(47))}{path}?{query}", timeout)


def fetch_depth(
    symbol: str,
    *,
    limit: int = 20,
    base_url: str = BASE_URL,
    transport: Callable[[str, float], Any] | None = None,
    timeout: float = 10.0,
    clock: Callable[[], datetime] | None = None,
) -> BookSnapshot:
    """One depth snapshot from production (or demo, for comparison)."""
    sym = normalize_symbol(symbol)
    levels = _check_levels(limit)
    payload = _get(base_url, DEPTH_PATH, {"symbol": sym, "limit": levels}, transport or _default_transport, timeout)
    return parse_depth(payload, sym, ts=(clock or (lambda: datetime.now(timezone.utc)))())


def fetch_book_ticker(symbol: str, *, base_url: str = BASE_URL, transport: Callable[[str, float], Any] | None = None, timeout: float = 10.0) -> dict[str, float]:
    """Best bid/ask only - the cheap call for live-vs-demo comparison."""
    sym = normalize_symbol(symbol)
    payload = _get(base_url, BOOK_TICKER_PATH, {"symbol": sym}, transport or _default_transport, timeout)
    if not isinstance(payload, dict):
        raise DepthError("bookTicker payload is not an object")
    try:
        return {"bid": float(payload["bidPrice"]), "ask": float(payload["askPrice"]),
                "bid_qty": float(payload["bidQty"]), "ask_qty": float(payload["askQty"])}
    except (KeyError, TypeError, ValueError):
        raise DepthError(f"malformed bookTicker payload: {payload!r}") from None


def fetch_agg_trades(symbol: str, *, limit: int = 100, base_url: str = BASE_URL, transport: Callable[[str, float], Any] | None = None, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Recent public trades, normalised to floats (no keys, no account)."""
    sym = normalize_symbol(symbol)
    if not 1 <= limit <= 1000:
        raise ValueError("aggTrades limit must be 1..1000")
    payload = _get(base_url, AGG_TRADES_PATH, {"symbol": sym, "limit": limit}, transport or _default_transport, timeout)
    if not isinstance(payload, list):
        raise DepthError("aggTrades payload is not a list")
    out = []
    for row in payload:
        try:
            out.append({"id": int(row["a"]), "price": float(row["p"]), "qty": float(row["q"]),
                        "ts": datetime.fromtimestamp(int(row["T"]) / 1000.0, tz=timezone.utc),
                        "maker": bool(row.get("m", False))})
        except (KeyError, TypeError, ValueError):
            raise DepthError(f"malformed agg trade: {row!r}") from None
    return out


def record_once(
    symbols: Sequence[str],
    *,
    limit: int = 20,
    conn: Any = None,
    transport: Callable[[str, float], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> list[BookSnapshot]:
    """Fetch one snapshot per symbol and persist it when a conn is given."""
    snaps = [fetch_depth(s, limit=limit, transport=transport, clock=clock) for s in symbols]
    if conn is not None and snaps:
        from .. import db

        for snap in snaps:
            db.insert_book_snapshot(conn, snap)
    return snaps


def run_recorder(
    symbols: Sequence[str],
    *,
    every: float = 60.0,
    limit: int = 20,
    conn: Any = None,
    transport: Callable[[str, float], Any] | None = None,
    on_snapshot: Callable[[list[BookSnapshot]], None] | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Sample forever (until ``stop`` is true). Returns cycles completed."""
    if every <= 0:
        raise ValueError("every must be positive")
    cycles = 0
    while not (stop and stop()):
        snaps = record_once(symbols, limit=limit, conn=conn, transport=transport, clock=clock)
        cycles += 1
        if on_snapshot:
            on_snapshot(snaps)
        if stop and stop():
            break
        sleep(every)
    return cycles
