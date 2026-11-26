"""Strategy registry.  Add your own subclass and register it here."""

from __future__ import annotations

from typing import Type

from .base import Action, Signal, Strategy
from .rsi_meanrev import RsiMeanReversion
from .sma_cross import SmaCross

REGISTRY: dict[str, Type[Strategy]] = {
    SmaCross.name: SmaCross,
    RsiMeanReversion.name: RsiMeanReversion,
}


def build(name: str, **params: object) -> Strategy:
    """Instantiate a strategy by registry name."""
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)  # type: ignore[arg-type]


__all__ = ["Action", "REGISTRY", "RsiMeanReversion", "Signal", "SmaCross", "Strategy", "build"]
