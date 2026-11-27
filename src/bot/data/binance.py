'''Binance spot klines: the venue the crypto books were researched on.

Public REST endpoint, no key, no account - read-only market data.  Alpaca's
crypto feed tracks the same market but from a different venue, and the 4h
prices differ by a fraction of a percent.  That fraction is enough to move a
SMA-cross fill by one bar, and one bar is the difference between +372% and
+184% over three years, so the live books must read the same venue the
research read.
'''

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .feed import Bar

BASE_URL = "https://api.binance.com/api/v3/klines"
MAX_LIMIT = 1000

#: Binance interval strings we accept, with the bar span each one means.
INTERVALS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
}


def _ms(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1000)


def _get(url: str, timeout: float = 20.0) -> Any:
    '''One HTTP GET returning parsed JSON (kept separate so tests can patch it).'''
    req = urllib.request.Request(url, headers={"User-Agent": "trading-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed host
        return json.loads(resp.read().decode("utf-8"))


def parse_kline(row: Any) -> Bar:
    '''[openTime, open, high, low, close, volume, closeTime, ...] -> Bar.'''
    return Bar(
        ts=datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc),
        open=float(row[1]), high=float(row[2]), low=float(row[3]),
        close=float(row[4]), volume=float(row[5]),
    )


def fetch_binance_bars(
    symbol: str,
    interval: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = MAX_LIMIT,
    timeout: float = 20.0,
) -> list[Bar]:
    '''Bars for one symbol, oldest first, paginated 1000 at a time.

    With ``start`` given every page is fetched from there forward until the
    exchange stops returning rows; without it the newest ``limit`` bars come
    back in one call.
    '''
    span = INTERVALS.get(interval)
    if span is None:
        raise ValueError(f"unsupported interval: {interval}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    sym = symbol.replace("/", "").replace("-", "").upper()

    rows: list[Any] = []
    cursor = start
    while True:
        params = [f"symbol={sym}", f"interval={interval}", f"limit={limit}"]
        if cursor is not None:
            params.append(f"startTime={_ms(cursor)}")
        if end is not None:
            params.append(f"endTime={_ms(end)}")
        page = _get(BASE_URL + "?" + "&".join(params), timeout=timeout)
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if cursor is None or len(page) < limit:
            break
        last_close = datetime.fromtimestamp(int(page[-1][6]) / 1000.0, tz=timezone.utc)
        nxt = last_close + timedelta(milliseconds=1)
        if nxt <= cursor:          # safety: never spin on a stuck cursor
            break
        cursor = nxt
        if end is not None and cursor >= end:
            break
    if not rows:
        return []

    seen: dict[datetime, Bar] = {}
    for row in rows:
        try:
            bar = parse_kline(row)
        except (TypeError, ValueError, IndexError):
            continue
        seen[bar.ts] = bar
    bars = [seen[k] for k in sorted(seen)]
    if start is not None:
        bars = [b for b in bars if b.ts >= start]
    if end is not None:
        bars = [b for b in bars if b.ts <= end]
    return bars