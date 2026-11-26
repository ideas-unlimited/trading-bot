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
