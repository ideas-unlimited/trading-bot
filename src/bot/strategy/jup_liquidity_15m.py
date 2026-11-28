"""JUP multi-timeframe demand-zone sleeve, 15m execution (paper probe).

Researched on JUP/USDT Binance spot, 4h decisions with 15m execution,
2025-11-28 .. 2026-11-27 UTC (last 12 months; sub-4h cap).  The researched
rule buys three equal tranches as price enters an overlapping 4h/6h/1d
demand cluster, puts the stop below the cluster, uses the next confirmed
15m demand band centre as the first target, and trails the whole position
1.5 x ATR(15m) after that target.  That DCA/trail variant made +16.6R on
55 trades (67.3% wins, PF 1.50) in the research simulator; the exact-3
timeframe bucket lost -2.6R, so this sleeve takes exactly two timeframes.

The production Engine supports one weighted-average position and a trail,
not a partial sale.  This sleeve therefore runs the strongest researched
exit (no sale at target; arm the trail when price touches the target).
Each tranche is 1/3 of the normal order and Signal.size_fraction is scaled
to 0.4, so the forward service default 5% risk becomes at most 2% total when
all three tranches fill.  JUP's tight stops often hit the 50% notional cap
first; the measured production replay therefore carries about 1% total risk,
not 2%.  The sleeve never uses more than the last 12 months of 15m data even
when the loader hands it a longer window.

This is a paper probe, not a promise: one year, one symbol, 55 trades, and
top-3 trades carry 80% of the research P&L.  See research/jup/.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from bot.analysis import atr, demand_bands

from .base import Action, Signal, Strategy, require_columns


OHLCV = ("open", "high", "low", "close", "volume")


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Completed higher-timeframe bars, labelled by their close time."""
    x = df.set_index(pd.DatetimeIndex(df["ts"]))
    # The 15m index is bar *open* time.  A right-labelled, left-closed bin
    # [12:00, 16:00) is therefore the completed 4h bar whose close is 16:00;
    # closed="right" would incorrectly pull the next 16:00 15m bar into it.
    kwargs: dict[str, str] = {"label": "right", "closed": "left"}
    if rule != "1D":
        # A daily bucket already starts at midnight UTC; passing origin there
        # only triggers pandas' tz-aware warning without changing the bins.
        kwargs["origin"] = "epoch"
    out = x.resample(rule, **kwargs).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    out.index.name = "ts"
    return out


def _zone_rows(df: pd.DataFrame, tf: str, *, swing: int, half_atr: float,
               max_age_days: float) -> list[dict[str, Any]]:
    bands = demand_bands(df, swing=swing, half_atr=half_atr,
                         max_age_days=max_age_days)
    return [
        {
            "tf": tf, "center": float(r.center), "lower": float(r.lower),
            "upper": float(r.upper), "available_at": r.available_at,
            "expires_at": r.expires_at,
        }
        for r in bands.itertuples(index=False)
    ]


def _clusters(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlapping demand bands, grouped by price overlap."""
    out: list[dict[str, Any]] = []
    for z in sorted(active, key=lambda x: (x["lower"], x["upper"])):
        placed = False
        for c in out:
            if z["lower"] <= c["upper"] and z["upper"] >= c["lower"]:
                lower = max(c["lower"], z["lower"])
                upper = min(c["upper"], z["upper"])
                if lower <= upper:
                    c["lower"], c["upper"] = lower, upper
                    c["zones"].append(z)
                    placed = True
                    break
        if not placed:
            out.append({"lower": z["lower"], "upper": z["upper"], "zones": [z]})
    for c in out:
        c["tfs"] = tuple(sorted({z["tf"] for z in c["zones"]}))
        c["conf"] = len(c["tfs"])
    return out


class JupLiquidity15m(Strategy):
    """JUP demand-cluster DCA with a 15m structural target and trail."""

    name = "jup_liquidity_15m"
    warmup = 0
    PARAMS = (
        "min_conf", "max_conf", "stop_atr", "trail_mult", "max_hold_bars",
        "entry_window_bars", "zone_swing", "zone_half_atr",
        "target_age_days", "zone_break_atr", "risk_scale",
        "max_dist_atr", "min_width_atr",
    )

    def __init__(
        self,
        min_conf: int = 2,
        max_conf: int = 2,
        stop_atr: float = 1.0,
        trail_mult: float = 1.5,
        max_hold_bars: int = 480,
        entry_window_bars: int = 288,
        zone_swing: int = 5,
        zone_half_atr: float = 0.5,
        target_age_days: float = 3.0,
        zone_break_atr: float = 0.75,
        risk_scale: float = 0.4,
        max_dist_atr: float = 3.0,
        min_width_atr: float = 0.15,
    ) -> None:
        super().__init__(
            min_conf=min_conf, max_conf=max_conf, stop_atr=stop_atr,
            trail_mult=trail_mult, max_hold_bars=max_hold_bars,
            entry_window_bars=entry_window_bars, zone_swing=zone_swing,
            zone_half_atr=zone_half_atr, target_age_days=target_age_days,
            zone_break_atr=zone_break_atr, risk_scale=risk_scale,
            max_dist_atr=max_dist_atr, min_width_atr=min_width_atr,
        )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = require_columns(df, OHLCV + ("ts",)).copy()
        if len(df) > 1:
            cutoff = df["ts"].max() - pd.Timedelta(days=365)
            df = df[df["ts"] >= cutoff].reset_index(drop=True)
        df["atr"] = atr(df["high"], df["low"], df["close"], 14)
        self._setups: list[dict[str, Any]] = []
        self._next = 0
        self._setup: dict[str, Any] | None = None
        self._ordered = False
        self._entry_i: int | None = None
        self._first_signal_i: int | None = None
        self._had_position = False

        d4 = _resample(df, "4h")
        d6 = _resample(df, "6h")
        d1 = _resample(df, "1D")
        d15 = df.set_index(pd.DatetimeIndex(df["ts"]))
        if len(d4) < 30 or len(d6) < 30 or len(d1) < 20:
            return df

        zones: dict[str, list[dict[str, Any]]] = {
            "4h": _zone_rows(d4, "4h", swing=self.zone_swing,
                             half_atr=self.zone_half_atr, max_age_days=60),
            "6h": _zone_rows(d6, "6h", swing=self.zone_swing,
                             half_atr=self.zone_half_atr, max_age_days=60),
            "1d": _zone_rows(d1, "1d", swing=self.zone_swing,
                             half_atr=self.zone_half_atr, max_age_days=60),
        }
        z15 = _zone_rows(d15, "15m", swing=self.zone_swing,
                         half_atr=self.zone_half_atr,
                         max_age_days=self.target_age_days)
        a4 = atr(d4["high"], d4["low"], d4["close"], 14)
        broken = {tf: [False] * len(zs) for tf, zs in zones.items()}

        for i, ts in enumerate(d4.index):
            close = float(d4["close"].iloc[i])
            av = float(a4.iloc[i])
            if not np.isfinite(av) or av <= 0:
                continue
            active: list[dict[str, Any]] = []
            for tf, zs in zones.items():
                for j, z in enumerate(zs):
                    if broken[tf][j]:
                        continue
                    if ts >= z["expires_at"]:
                        broken[tf][j] = True
                    elif ts >= z["available_at"] and close < z["lower"] - self.zone_break_atr * av:
                        broken[tf][j] = True
                    elif ts >= z["available_at"] and close >= z["lower"]:
                        active.append(z)
            # Research filters the eligible clusters first, then picks the
            # highest-confluence, closest one.  Filtering after the sort would
            # silently skip a trade whenever the top cluster is overhead.
            clusters = [c for c in _clusters(active)
                        if self.min_conf <= c["conf"] <= self.max_conf
                        and float(c["upper"]) < close
                        and (close - float(c["upper"])) / av <= self.max_dist_atr
                        and (float(c["upper"]) - float(c["lower"])) / av >= self.min_width_atr]
            if not clusters:
                continue
            clusters.sort(
                key=lambda c: (c["conf"], -((close - c["upper"]) / av)),
                reverse=True,
            )
            cl = clusters[0]
            e1 = float(cl["upper"])
            stop = float(cl["lower"]) - self.stop_atr * av
            if stop <= 0 or e1 <= stop:
                continue
            cand = [
                z for z in z15
                if z["available_at"] <= ts < z["expires_at"]
                and z["lower"] > close * 1.002 and z["lower"] > e1 * 1.002
            ]
            if not cand:
                continue
            z = min(cand, key=lambda x: x["lower"])
            target = float(z["center"])
            if target <= e1:
                continue
            self._setups.append({
                "ts": ts, "expires_at": ts + pd.Timedelta(
                    minutes=15 * self.entry_window_bars),
                "e1": e1, "mid": 0.5 * (e1 + float(cl["lower"])),
                "lower": float(cl["lower"]), "stop": stop,
                "levels": (e1, 0.5 * (e1 + float(cl["lower"])), float(cl["lower"])),
                "target": target, "conf": cl["conf"], "tfs": cl["tfs"],
            })
        return df

    def _clear(self) -> None:
        self._setup = None
        self._ordered = False
        self._entry_i = None
        self._first_signal_i = None

    def _ladder(self, i: int) -> Signal:
        s = self._setup
        assert s is not None
        return Signal(
            Action.BUY,
            reason=f"JUP demand {s['conf']}TF {s['tfs']} ladder",
            stop_loss=float(s["stop"]),
            trail_atr=self.trail_mult,
            trail_after_price=float(s["target"]),
            size_fraction=self.risk_scale / 3.0,
            limit_prices=tuple(float(x) for x in s["levels"]),
            limit_expires_at=s["expires_at"],
        )

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]
        ts = row["ts"]

        if self._had_position and position <= 1e-12:
            self._clear()
        self._had_position = position > 1e-12

        if (self._setup is not None and position <= 1e-12
                and ts >= self._setup["expires_at"]):
            self._setup = None
            self._ordered = False

        while self._next < len(self._setups) and self._setups[self._next]["ts"] <= ts:
            s = self._setups[self._next]
            self._next += 1
            if position <= 1e-12 and self._setup is None:
                self._setup = s
                self._ordered = False

        if self._setup is None:
            return None
        s = self._setup

        if position > 1e-12 and self._entry_i is None:
            self._entry_i = self._first_signal_i if self._first_signal_i is not None else i

        if position > 1e-12 and self._entry_i is not None:
            if i - self._entry_i >= self.max_hold_bars:
                return Signal(Action.CLOSE, reason=f"time exit after {self.max_hold_bars} bars")
            return None

        if self._ordered:
            return None
        self._ordered = True
        self._first_signal_i = i
        return self._ladder(i)

