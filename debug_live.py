"""Throwaway debug script for test_buy_order_with_bracket_stop (deleted after use)."""
import io
from datetime import datetime, timedelta, timezone

from tests.test_live import _FakeClient, _payload
from bot.live import LiveTrader
from bot.strategy import build
from bot.config import BotConfig

client = _FakeClient(_payload())
base = datetime(2024, 1, 5, tzinfo=timezone.utc)


def price(k: int) -> float:
    return 104.0 - k if k < 60 else 44.0 + (k - 60)


extra = [
    {
        "t": (base + timedelta(days=k)).strftime("%Y-%m-%dT00:00:00Z"),
        "o": price(k), "h": price(k) + 1, "l": price(k) - 1, "c": price(k), "v": 1000.0,
    }
    for k in range(120)
]
client.payload = {"bars": _payload()["bars"] + extra}
log = io.StringIO()
trader = LiveTrader(
    client, build("sma_cross"), BotConfig(), "SPY", "1d",
    state_path="runs/state_debug.json", stream=log, clock=lambda s: None,
)
trader.poll(now=datetime(2024, 3, 10, tzinfo=timezone.utc))
print("orders:", client.orders)
lines = log.getvalue().splitlines()
print("\n".join(lines[:3]))
print("...")
print("\n".join(lines[-6:]))
