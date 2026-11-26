"""Position sizing and capital protection.

Sizing rule: risk a fixed fraction of equity between the entry price and the
stop, then cap the position by ``max_position_pct``.  This means a volatile
asset gets a smaller position for the same dollar risk, which is the whole
point of the exercise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import RiskConfig

#: Positions worth less than this are not worth the fees.
MIN_NOTIONAL = 10.0


@dataclass
class RiskManager:
    config: RiskConfig = field(default_factory=RiskConfig)
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def update_equity(self, equity: float) -> None:
        """Track the high-water mark and trip the kill switch on deep drawdown."""
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.halted or self.peak_equity <= 0:
            return
        drawdown = 1.0 - equity / self.peak_equity
        if drawdown >= self.config.max_drawdown_stop:
            self.halted = True
            self.halt_reason = f"max drawdown {drawdown:.1%} reached"

    def size(self, equity: float, price: float, stop_loss: float | None) -> float:
        """Quantity to buy for a new long, or 0.0 if we should not trade."""
        if self.halted or equity <= 0 or price <= 0:
            return 0.0

        if stop_loss is not None and 0 < stop_loss < price:
            per_unit_risk = price - stop_loss
            qty = (equity * self.config.risk_per_trade) / per_unit_risk
        else:
            # No stop: fall back to a plain allocation cap.
            qty = (equity * self.config.max_position_pct) / price

        max_qty = (equity * self.config.max_position_pct * self.config.max_leverage) / price
        qty = min(qty, max_qty)
        if qty * price < MIN_NOTIONAL:
            return 0.0
        return qty
