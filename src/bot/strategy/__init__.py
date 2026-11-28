"""Strategy registry.  Add your own subclass and register it here."""

from __future__ import annotations

from typing import Type

from .base import Action, Signal, Strategy
from .capitulation_dip_uni import CapitulationDipUni
from .donchian import DonchianBreakout
from .jup_liquidity_15m import JupLiquidity15m
from .momentum_swing import MomentumSwing
from .rsi_meanrev import RsiMeanReversion
from .sma_cross import SmaCross
from .volume_climax import VolumeClimax
from .wvfx_stoch import WilliamVixFixStoch

REGISTRY: dict[str, Type[Strategy]] = {
    SmaCross.name: SmaCross,
    CapitulationDipUni.name: CapitulationDipUni,
    RsiMeanReversion.name: RsiMeanReversion,
    DonchianBreakout.name: DonchianBreakout,
    MomentumSwing.name: MomentumSwing,
    JupLiquidity15m.name: JupLiquidity15m,
    VolumeClimax.name: VolumeClimax,
    WilliamVixFixStoch.name: WilliamVixFixStoch,
}


def build(name: str, **params: object) -> Strategy:
    """Instantiate a strategy by registry name."""
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)  # type: ignore[arg-type]


__all__ = ["Action", "CapitulationDipUni", "DonchianBreakout", "JupLiquidity15m", "MomentumSwing", "REGISTRY", "RsiMeanReversion", "Signal", "SmaCross", "Strategy", "VolumeClimax", "WilliamVixFixStoch", "build"]