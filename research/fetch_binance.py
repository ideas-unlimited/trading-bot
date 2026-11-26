
"""Fetch Binance spot klines and store them in the repo's CSV format."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

BASE = "https://api.binance.com/api/v3/klines"
OUT = Path(__file__).resolve().parents[1] / "data" / "crypto"

TF_MS = {"4h": 4 * 3600 * 1000, "1d": 24 * 3600 * 1000}
START = {"4h": 1514764800000, "1d": 1502928000000}  # 2018-01-01 / 2017-08-17


def get(symbol: str, interval: str, start_ms: int, limit: int = 1000) -> list[list]:
    url = f"{BASE}?symbol={symbol}&interval={interval}&startTime={start_ms}&limit={limit}"
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def fetch(symbol: str, interval: str) -> int:
    rows: list[list] = []
    start = START[interval]
    while True:
        batch = get(symbol, interval, start)
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + TF_MS[interval]
        if nxt <= start or len(batch) < 1000:
            break
        start = nxt
        time.sleep(0.12)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{symbol}_{interval}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for r in rows:
            fh.write(f"{int(r[0]) // 1000},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
    return len(rows)


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
        for tf in ("4h", "1d"):
            n = fetch(sym, tf)
            print(f"{sym:10s} {tf:3s} {n:6d} bars")
