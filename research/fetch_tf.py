"""Fetch one crypto timeframe into data/crypto/<SYM>_<tf>.csv (3-year window).

Scratch research helper: mirrors research/fetch_binance.py but takes the
interval from the command line so 15m / 1h comparisons use the same store.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://api.binance.com/api/v3/klines"
OUT = Path("data/crypto")
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


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


def main(interval: str, since: str) -> int:
    ms = int(time.mktime(time.strptime(since, "%Y-%m-%d")) * 1000)
    step = {"15m": 900_000, "1h": 3_600_000}[interval]
    for sym in COINS:
        rows: list[list] = []
        start = ms
        while True:
            batch = get(sym, interval, start)
            if not batch:
                break
            rows.extend(batch)
            nxt = batch[-1][0] + step
            if nxt <= start or len(batch) < 1000:
                break
            start = nxt
        path = OUT / f"{sym}_{interval}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for r in rows:
                w.writerow([int(r[0] // 1000), r[1], r[2], r[3], r[4], r[5]])
        print(sym, interval, len(rows), flush=True)
    return 0


if __name__ == "__main__":
    rais = sys.argv[1] if len(sys.argv) > 1 else "15m"
    print("fetching", rais, flush=True)
    sys.exit(main(rais, "2023-09-01"))