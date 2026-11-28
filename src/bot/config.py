"""Runtime configuration for the bot.

Everything here is deliberately conservative.  The defaults describe a
*simulation*: no network calls, no API keys, no real orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CostConfig:
    """Execution costs charged on every fill."""

    fee_bps: float = 10.0  # 10 bps = 0.10% taker fee
    slippage_bps: float = 5.0  # assumed adverse price impact


@dataclass(frozen=True)
class RiskConfig:
    """Position sizing and capital-protection limits."""

    risk_per_trade: float = 0.01  # 1% of equity risked between entry and stop
    max_position_pct: float = 0.95  # never allocate more than this fraction
    max_leverage: float = 1.0  # set > 1 only if you really mean it
    # Fraction of peak equity that, once lost, halts new entries.
    max_drawdown_stop: float = 0.10


@dataclass(frozen=True)
class BotConfig:
    initial_cash: float = 10_000.0
    costs: CostConfig = field(default_factory=CostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    # Bars per year, used only to annualise metrics.
    bars_per_year: float = 365.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 < self.risk.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if self.risk.max_position_pct <= 0:
            raise ValueError("max_position_pct must be positive")
