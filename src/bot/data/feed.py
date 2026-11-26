"""Price data: a common bar type, CSV loading and a synthetic generator.

The synthetic generator exists so the project is fully runnable offline and
so tests are deterministic.  It is a random walk with fat-ish tails and
trending regimes -- it is *not* a model of any real market.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"bar {self.ts}: high {self.high} < low {self.low}")
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= 0:
                raise ValueError(f"bar {self.ts}: {name} must be positive")


def write_bars(path: str | Path, bars: Sequence[Bar]) -> Path:
    """Write bars to a plain OHLCV csv."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow(
                [b.ts.isoformat(), f"{b.open:.8f}", f"{b.high:.8f}",
                 f"{b.low:.8f}", f"{b.close:.8f}", f"{b.volume:.8f}"]
            )
    return path


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    # Accept plain ISO dates, ISO datetimes and unix epoch seconds/millis.
    try:
        value = float(raw)
    except ValueError:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if value > 1e11:  # milliseconds
        value /= 1000.0
    return datetime.fromtimestamp(value, tz=timezone.utc)


def load_csv(path: str | Path) -> list[Bar]:
    """Load an OHLCV csv (columns: timestamp, open, high, low, close, volume)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"data file not found: {path}")
    bars: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty csv")
        names = {n.strip().lower() for n in reader.fieldnames}
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required - names
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            if not (row.get("timestamp") or "").strip():
                continue
            bars.append(
                Bar(
                    ts=_parse_ts(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
    if not bars:
        raise ValueError(f"{path}: no bars parsed")
    bars.sort(key=lambda b: b.ts)
    return bars


def iter_csv(path: str | Path) -> Iterator[Bar]:
    yield from load_csv(path)


def generate_synthetic(
    n: int = 5000,
    *,
    seed: int = 7,
    start_price: float = 100.0,
    start: datetime | None = None,
    step: timedelta = timedelta(hours=1),
    vol: float = 0.004,
    regime_len: int = 400,
) -> list[Bar]:
    """Deterministic random-walk OHLCV series with alternating drift regimes."""
    import random

    if n <= 0:
        raise ValueError("n must be positive")
    rng = random.Random(seed)
    start = start or datetime(2023, 1, 1, tzinfo=timezone.utc)
    price = start_price
    drift = 0.0
    bars: list[Bar] = []

    for i in range(n):
        if i % regime_len == 0:
            # New regime: bull, bear or chop, with a moderate drift.
            drift = rng.choice([0.00012, 0.00009, -0.00011, -0.00008, 0.0])
        shock = rng.gauss(0.0, 1.0)
        # Occasional fat tail.
        if rng.random() < 0.01:
            shock *= 3.0
        open_px = price
        close_px = max(0.01, open_px * (1.0 + drift + vol * shock))
        wick = abs(rng.gauss(0.0, vol * 0.6)) * open_px
        high = max(open_px, close_px) + wick
        low = max(0.01, min(open_px, close_px) - abs(rng.gauss(0.0, vol * 0.6)) * open_px)
        volume = max(1.0, rng.gauss(1000.0, 250.0))
        bars.append(
            Bar(
                ts=start + step * i,
                open=round(open_px, 8),
                high=round(high, 8),
                low=round(low, 8),
                close=round(close_px, 8),
                volume=round(volume, 2),
            )
        )
        price = close_px
    return bars


class Feed:
    """Replays a bar sequence.  One call to ``next`` == one time step."""

    def __init__(self, bars: Iterable[Bar]):
        self._bars = list(bars)
        if not self._bars:
            raise ValueError("feed needs at least one bar")
        self._i = 0

    def __iter__(self) -> Iterator[Bar]:
        return self

    def __next__(self) -> Bar:
        if self._i >= len(self._bars):
            raise StopIteration
        bar = self._bars[self._i]
        self._i += 1
        return bar

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def bars(self) -> list[Bar]:
        return list(self._bars)
