"""Strategy interface.

A strategy does two things:

1. ``prepare(df)`` adds indicator columns.  This runs once, vectorised, and
   must only ever use data at or before the current row (no lookahead).
2. ``signal(i, df, position)`` is called once per bar with the row index and
   the current position.  It returns a ``Signal`` or ``None``.

The engine executes whatever signal appears on bar *i* at the **open of bar
i+1**, so a strategy can never trade on a price it could not have seen.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class Signal:
    action: Action
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    #: Trail the stop this many ATRs below the highest high since entry.
    #: The engine only ever ratchets the stop upwards.  None disables it.
    trail_atr: float | None = None
    #: Arm the trail only after the trade shows this many R of profit
    #: (0 = arm immediately).  Measured on the close, like ``book.py``.
    trail_after_r: float = 0.0
    #: Arm the trail only after price trades at or above this level.
    #: Useful when the target is a structural level rather than an R multiple.
    trail_after_price: float | None = None
    #: A BUY may add to an existing position when this is true.  The engine
    #: keeps one weighted-average position and never opens a second one.
    add: bool = False
    #: Fraction of the normal risk-sized order to buy.  Three equal tranches
    #: use 1/3 each; the stop is shared by the whole position.
    size_fraction: float = 1.0
    #: Resting limit ladder.  When non-empty the engine does not buy at the
    #: next open; it fills each level touched by a later bar's low (at the
    #: level, or better at that bar's open), one size_fraction per level.
    limit_prices: tuple[float, ...] = ()
    #: The ladder is cancelled at or after this close timestamp.
    limit_expires_at: datetime | None = None


class Strategy(ABC):
    """Base class for all strategies."""

    name: str = "strategy"
    #: Bars that must elapse before signals are meaningful (indicator warmup).
    warmup: int = 0
    #: Tunable parameter names; subclasses must list what they accept so that
    #: a typo in ``--param`` fails loudly instead of being silently ignored.
    PARAMS: tuple[str, ...] = ()

    def __init__(self, **params: object) -> None:
        unknown = sorted(set(params) - set(self.PARAMS))
        if unknown:
            raise TypeError(
                f"{type(self).__name__}: unknown parameter(s) {unknown}; "
                f"available: {list(self.PARAMS)}"
            )
        for key, value in params.items():
            setattr(self, key, value)
        self.params: dict[str, object] = {k: getattr(self, k) for k in self.PARAMS}

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return ``df`` with any indicator columns added."""

    @abstractmethod
    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        """Decide what to do at the close of bar ``i`` given open ``position``."""

    def __repr__(self) -> str:
        params = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{type(self).__name__}({params})"


def require_columns(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.DataFrame:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"dataframe is missing columns: {missing}")
    return df
