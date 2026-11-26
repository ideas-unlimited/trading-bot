"""Fetch 1h klines for the 3-year window (2023-08 onward)."""

from __future__ import annotations

import json, time, urllib.request
from pathlib import Path

BASE = 'https://api.binance.com/api/v3/klines'
OUT = Path(__file__).resolve().parents[1] / 'data' / 'crypto'
COINS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'SOLUSDT']
START = 1690848000000  # 2023-08-01
STEP = 3600 * 1000


def get(symbol, start, limit=1000):
    url = f'{BASE}?symbol={symbol}&interval=1h&startTime={start}&limit={limit}'
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def main():
    for sym in COINS:
        rows, start = [], START
        while True:
            batch = get(sym, start)
            if not batch:
                break
            rows.extend(batch)
            nxt = batch[-1][0] + STEP
            if nxt <= start or len(batch) < 1000:
                break
            start = nxt
        path = OUT / f'{sym}_1h.csv'
        with path.open('w', encoding='utf-8', newline='') as f:
            f.write('timestamp,open,high,low,close,volume\n')
            for r in rows:
                f.write(f'{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n')
        print(sym, len(rows), 'bars')


if __name__ == "__main__":
    main()
