"""A minimal long-only brokerage simulator.

It models the things that actually decide whether a backtest is honest:

* fees on both sides (``fee_bps``),
* adverse slippage on both sides (``slippage_bps``),
* cash never going negative,
* realised PnL accounted per round-trip trade.

Shorting, margin and partial fills are intentionally not supported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import CostConfig


@dataclass(slots=True)
class Fill:
    ts: datetime
    side: str  # "buy" | "sell"
    qty: float
    price: float  # price actually paid/received, after slippage
    fee: float
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass(slots=True)
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    qty: float
    entry_price: float
    exit_price: float
    fees: float
    reason_open: str = ""
    reason_close: str = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty - self.fees

    @property
    def return_pct(self) -> float:
        cost = self.entry_price * self.qty
        return self.pnl / cost if cost else 0.0


@dataclass
class Broker:
    cash: float
    costs: CostConfig = field(default_factory=CostConfig)
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: datetime | None = None
    entry_reason: str = ""
    entry_fee: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    # -- pricing helpers -------------------------------------------------
    def _buy_price(self, price: float) -> float:
        return price * (1.0 + self.costs.slippage_bps / 10_000.0)

    def _sell_price(self, price: float) -> float:
        return price * (1.0 - self.costs.slippage_bps / 10_000.0)

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.costs.fee_bps / 10_000.0

    # -- state -----------------------------------------------------------
    @property
    def in_position(self) -> bool:
        return self.qty > 1e-12

    def market_value(self, price: float) -> float:
        return self.qty * price

    def equity(self, price: float) -> float:
        return self.cash + self.market_value(price)

    def affordable_qty(self, price: float) -> float:
        """Largest quantity we can buy at ``price`` including fees."""
        unit = self._buy_price(price)
        unit_cost = unit * (1.0 + self.costs.fee_bps / 10_000.0)
        return max(0.0, self.cash / unit_cost) if unit_cost > 0 else 0.0

    # -- orders ----------------------------------------------------------
    def buy(self, ts: datetime, price: float, qty: float, reason: str = "") -> Fill | None:
        qty = min(qty, self.affordable_qty(price))
        if qty <= 1e-12:
            return None
        fill_price = self._buy_price(price)
        fee = self._fee(qty * fill_price)
        self.cash -= qty * fill_price + fee
        # Weighted average entry (only ever one add-on per position here).
        if self.in_position:
            total_qty = self.qty + qty
            self.entry_price = (self.entry_price * self.qty + fill_price * qty) / total_qty
            self.qty = total_qty
            self.entry_fee += fee
        else:
            self.qty = qty
            self.entry_price = fill_price
            self.entry_ts = ts
            self.entry_reason = reason
            self.entry_fee = fee
        fill = Fill(ts, "buy", qty, fill_price, fee, reason)
        self.fills.append(fill)
        return fill

    def sell(self, ts: datetime, price: float, reason: str = "", qty: float | None = None) -> Fill | None:
        """Sell ``qty`` (default: whole position)."""
        qty = self.qty if qty is None else min(qty, self.qty)
        if qty <= 1e-12:
            return None
        fill_price = self._sell_price(price)
        fee = self._fee(qty * fill_price)
        self.cash += qty * fill_price - fee

        # Attribute fees pro-rata if the position is only partly closed.
        portion = qty / self.qty if self.qty else 1.0
        entry_fee = self.entry_fee * portion
        if self.entry_ts is not None:
            self.trades.append(
                Trade(
                    entry_ts=self.entry_ts,
                    exit_ts=ts,
                    qty=qty,
                    entry_price=self.entry_price,
                    exit_price=fill_price,
                    fees=entry_fee + fee,
                    reason_open=self.entry_reason,
                    reason_close=reason,
                )
            )
        self.qty -= qty
        self.entry_fee -= entry_fee
        if self.qty <= 1e-12:
            self.qty = 0.0
            self.entry_price = 0.0
            self.entry_ts = None
            self.entry_reason = ""
            self.entry_fee = 0.0
        fill = Fill(ts, "sell", qty, fill_price, fee, reason)
        self.fills.append(fill)
        return fill

    @property
    def fees_paid(self) -> float:
        return sum(f.fee for f in self.fills)
