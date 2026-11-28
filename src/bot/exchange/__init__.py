"""Broker abstraction — one interface, venue-specific clients.

The engine never talks to an exchange directly; it talks to a broker. One
venue: Binance spot, read-only production klines for signals, demo trading for
orders.  ``BinanceLiveClient`` is the duck-typed adapter the loop/CLI/web use.

Honesty rules that live in this package:

* **Spot only.** Long-only cash balances; no futures, no margin, no shorts.
* **Demo by default.** A Binance broker defaults to demo trading and needs an
  explicit base URL to reach production, so an accidental live order takes
  two deliberate changes, not one.
* **Idempotent order ids.** Every order carries a deterministic
  clientOrderId derived from the signal, so a restart cannot double-fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# NOTE: this package is deliberately NOT named bot.broker — that module is the
# backtest fill simulator (Fill/Trade). Here we talk to a real venue.


class BrokerError(RuntimeError):
    """A rejected broker call, with the venue's own error code when known."""

    def __init__(self, message: str, *, code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


@dataclass(frozen=True)
class Balance:
    asset: str
    free: float
    locked: float = 0.0

    @property
    def total(self) -> float:
        return self.free + self.locked


@dataclass(frozen=True)
class SymbolFilters:
    """Exchange rules needed to build a valid order."""

    symbol: str
    step_size: float
    tick_size: float
    min_qty: float = 0.0
    min_notional: float = 0.0

    def floor_qty(self, qty: float) -> float:
        if self.step_size <= 0:
            return qty
        steps = int(qty / self.step_size + 1e-9)
        return round(steps * self.step_size, 12)

    def floor_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        ticks = int(price / self.tick_size + 1e-9)
        return round(ticks * self.tick_size, 12)


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: str
    order_type: str
    order_id: int | str | None = None
    client_order_id: str | None = None
    status: str = ""
    executed_qty: float = 0.0
    quote_qty: float = 0.0
    price: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def filled(self) -> bool:
        return self.status in {"FILLED", "PARTIALLY_FILLED"} and self.executed_qty > 0


__all__ = [
    "Balance",
    "BrokerError",
    "OrderResult",
    "SymbolFilters",
]
