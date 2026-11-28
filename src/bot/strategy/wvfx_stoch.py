"""William Vix Fix + Stochastic confluence long (William_Vix_Fix_Stoch).

Port of the user-described setup (2026-11-27), combining two TradingView
studies pasted by the user - CM_Williams_Vix_Fix (LazyBear) and the built-in
Stochastic.  Re-analysed on 2026-11-27 against the three-year 4h Binance
sample (2023-11-26..2026-11-26).

The setup, read literally:

1. **WVF green bar** - ``wvf >= upper_band`` or ``wvf >= range_high``.  The
   first green bar of a cluster is the "flash": it is the bar where the
   panic reading first appears, not every bar while it stays green.
   ``green_mode="any"`` takes every green bar instead (the literal reading);
   measured 2026-11-28 it loses -49.5% over two years on five coins against
   +20.4% for the first-bar reading, because the later bars of a panic
   cluster are a falling knife.  It exists to be tested, not deployed.
2. **Stochastic oversold** - %K below ``oversold`` (20) at some point in the
   last ``oversold_window`` bars.  The cross can happen after the low, so a
   short look-back is used rather than requiring %K < 20 on the entry bar.
3. **Trigger** - %K (blue) at or above %D (red) on that first green bar
   (``trigger="state"``, the user's literal reading), or the stricter
   actual cross (``trigger="cross"``, the default; same return in the test
   with roughly half the drawdown).  Entry is the next bar's open.

Re-analysis found the original five-bar green window was the bug: entering
every green bar of a panic cluster loses money (3y 4h, all regimes: about
-45% on a $1,000 book).  Restricting entries to the first green bar plus the
stoch turn was profitable in every regime; adding the default 200-day
own-coin bear filter (``regime_days=200``) cut the sample to 72 trades but
improved the 3y result to about +52% at 2% risk per trade, with an 8.7%
max drawdown.  The edge is a capitulation-bounce / mean-reversion edge, so it
is long-only spot and only meaningful on 4h; the 1d sample is weak (+0.5%
all-regime, +7.2% with the filter) and should not be traded.

Exits: ATR stop below the signal close, an R-multiple target from the same
close, and a time exit after ``max_hold`` bars.  ``exit_cross`` is available
but off by default - exiting on the first %K<%D cross shortened the average
hold to ~3 bars and gave back most of the move.  Everything is causal: flags
use rolling windows of past-and-present bars only and the engine fills at
the next open.

The user's later 14/3/5 stochastic smoothing and RSI oversold flip are
supported through ``smooth_k``/``smooth_d`` and the optional
``require_rsi_flip`` gate.  Both remain off by default: on the two-year 4h
sample the extra smoothing cut the WVF setup from 118 to 33 events and the
RSI gate left only a handful of entries.  See ``research/STOCH_RSI_WVF.md``.
"""

from __future__ import annotations

import pandas as pd

from .. import analysis as ta
from .. import indicators as ind
from .base import Action, Signal, Strategy


class WilliamVixFixStoch(Strategy):
    """Buy the first WVF panic bar when the stochastic turns up while oversold."""

    name = "wvfx_stoch"
    PARAMS = (
        "lookback_high",
        "band_length",
        "band_mult",
        "percentile_window",
        "percentile_high",
        "k_length",
        "smooth_k",
        "smooth_d",
        "oversold",
        "oversold_window",
        "entry_window",
        "green_mode",
        "trigger",
        "regime_days",
        "stop_atr",
        "rr",
        "max_hold",
        "exit_cross",
        "rsi_window",
        "rsi_oversold",
        "rsi_overbought",
        "rsi_flip_window",
        "require_rsi_flip",
        "exit_rsi_overbought",
    )

    def __init__(
        self,
        lookback_high: int = 22,
        band_length: int = 20,
        band_mult: float = 2.0,
        percentile_window: int = 50,
        percentile_high: float = 0.85,
        k_length: int = 14,
        smooth_k: int = 1,
        smooth_d: int = 3,
        oversold: float = 20.0,
        oversold_window: int = 5,
        entry_window: int = 1,
        green_mode: str = "first",
        trigger: str = "cross",
        regime_days: float = 200.0,
        stop_atr: float = 2.0,
        rr: float = 2.0,
        max_hold: int = 40,
        exit_cross: bool = False,
        rsi_window: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        rsi_flip_window: int = 5,
        require_rsi_flip: bool = False,
        exit_rsi_overbought: bool = False,
    ) -> None:
        if lookback_high < 2 or band_length < 2 or percentile_window < 2:
            raise ValueError("window lengths must be at least 2")
        if band_mult <= 0 or not 0 < percentile_high <= 1.10:
            raise ValueError("band_mult must be > 0 and percentile_high in (0, 1.10]")
        if k_length < 2 or smooth_k < 1 or smooth_d < 1:
            raise ValueError("k_length >= 2 and smoothing >= 1 required")
        if not 0 < oversold < 100:
            raise ValueError("oversold must be in (0, 100)")
        if oversold_window < 1 or entry_window < 1:
            raise ValueError("oversold_window and entry_window must be >= 1")
        if trigger not in ("state", "cross"):
            raise ValueError("trigger must be 'state' or 'cross'")
        if green_mode not in ("first", "any"):
            raise ValueError("green_mode must be 'first' or 'any'")
        if regime_days < 0:
            raise ValueError("regime_days must be >= 0 (0 disables the filter)")
        if rr <= 0 or max_hold < 1 or stop_atr <= 0:
            raise ValueError("rr/max_hold/stop_atr must be positive")
        if rsi_window < 2 or rsi_flip_window < 1:
            raise ValueError("rsi_window >= 2 and rsi_flip_window >= 1 required")
        if not 0 < rsi_oversold < rsi_overbought < 100:
            raise ValueError("RSI thresholds must satisfy 0 < oversold < overbought < 100")
        super().__init__(
            lookback_high=lookback_high,
            band_length=band_length,
            band_mult=band_mult,
            percentile_window=percentile_window,
            percentile_high=percentile_high,
            k_length=k_length,
            smooth_k=smooth_k,
            smooth_d=smooth_d,
            oversold=oversold,
            oversold_window=oversold_window,
            entry_window=entry_window,
            green_mode=green_mode,
            trigger=trigger,
            regime_days=regime_days,
            stop_atr=stop_atr,
            rr=rr,
            max_hold=max_hold,
            exit_cross=exit_cross,
            rsi_window=rsi_window,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            rsi_flip_window=rsi_flip_window,
            require_rsi_flip=require_rsi_flip,
            exit_rsi_overbought=exit_rsi_overbought,
        )
        self._entry_i: int | None = None
        rsi_warm = rsi_window if (require_rsi_flip or exit_rsi_overbought) else 0
        self._base_warmup = (
            max(lookback_high, band_length, percentile_window, k_length, rsi_warm)
            + smooth_d
            + 2
        )
        self.warmup = self._base_warmup

    @staticmethod
    def _bars_per_day(df: pd.DataFrame) -> float:
        """Infer bar spacing from ``ts``; fall back to 4h (6 bars/day)."""
        if "ts" not in df.columns:
            return 6.0
        ts = pd.to_datetime(df["ts"])
        gaps = ts.diff().dropna().dt.total_seconds()
        step = float(gaps.median()) if len(gaps) else 0.0
        return 86_400.0 / step if step > 0 else 6.0

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        self._entry_i = None
        wvf = ta.williams_vix_fix(
            out,
            lookback_high=self.lookback_high,
            band_length=self.band_length,
            band_mult=self.band_mult,
            percentile_window=self.percentile_window,
            percentile_high=self.percentile_high,
        )
        st = ta.stochastic(
            out, k_length=self.k_length, smooth_k=self.smooth_k, smooth_d=self.smooth_d
        )
        out["wvf"] = wvf["wvf"]
        out["wvf_green"] = wvf["green"].astype("boolean")
        out["k"] = st["k"]
        out["d"] = st["d"]
        out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
        out["rsi"] = ind.rsi(out["close"], self.rsi_window)
        rsi_up = (
            (out["rsi"].shift(1) <= self.rsi_oversold)
            & (out["rsi"] > self.rsi_oversold)
        ).fillna(False).astype(bool)
        rsi_down = (
            (out["rsi"].shift(1) >= self.rsi_overbought)
            & (out["rsi"] < self.rsi_overbought)
        ).fillna(False).astype(bool)
        out["rsi_flip_up"] = rsi_up
        out["rsi_flip_down"] = rsi_down
        out["rsi_flip_up_recent"] = (
            rsi_up.rolling(self.rsi_flip_window, min_periods=1).max().astype(bool)
        )

        green = wvf["green"].fillna(False).astype(bool)
        out["green"] = green
        out["green_event"] = green & ~green.shift(1, fill_value=False)
        if self.green_mode == "any":
            # Literal reading: every bar that is green.  Loses money (see the
            # module docstring); kept so the claim stays testable.
            out["green_flash"] = green
        elif self.entry_window == 1:
            # The first green bar of a cluster is the flash.
            out["green_flash"] = out["green_event"]
        else:
            # Legacy loose reading: any green bar in the last N bars.
            out["green_flash"] = (
                green.rolling(self.entry_window, min_periods=1).max().astype(bool)
            )

        oversold_hit = (out["k"] < self.oversold).fillna(False).astype(bool)
        out["oversold_flash"] = (
            oversold_hit.rolling(self.oversold_window, min_periods=1)
            .max()
            .astype(bool)
        )
        out["k_cross_up"] = (
            (out["k"] >= out["d"]) & (out["k"].shift(1) < out["d"].shift(1))
        ).fillna(False).astype(bool)

        if self.regime_days > 0:
            bars_per_day = self._bars_per_day(out)
            regime_bars = max(2, int(round(self.regime_days * bars_per_day)))
            sma = out["close"].rolling(regime_bars, min_periods=regime_bars).mean()
            out["regime_sma"] = sma
            # The filter is a risk filter, not part of the three conditions:
            # only buy capitulations while the coin is below its long SMA.
            out["regime_bear"] = (out["close"] < sma).fillna(False).astype(bool)
            self.warmup = max(self._base_warmup, regime_bars)
        else:
            out["regime_sma"] = float("nan")
            out["regime_bear"] = False
            self.warmup = self._base_warmup
        return out

    def signal(self, i: int, df: pd.DataFrame, position: float) -> Signal | None:
        row = df.iloc[i]

        if position > 0:
            if self._entry_i is None:
                self._entry_i = i
            if i - self._entry_i >= self.max_hold:
                return Signal(action=Action.CLOSE, reason=f"time exit after {self.max_hold} bars")
            if self.exit_rsi_overbought and bool(row["rsi_flip_down"]):
                return Signal(
                    action=Action.CLOSE,
                    reason=f"RSI crossed back below {self.rsi_overbought:g}",
                )
            if self.exit_cross and pd.notna(row["k"]) and pd.notna(row["d"]) and row["k"] < row["d"]:
                return Signal(action=Action.CLOSE, reason="stochastic %K crossed below %D")
            return None

        self._entry_i = None
        if pd.isna(row["k"]) or pd.isna(row["d"]) or pd.isna(row["atr"]):
            return None
        if self.regime_days > 0 and not bool(row["regime_bear"]):
            return None
        if not bool(row["green_flash"]) or not bool(row["oversold_flash"]):
            return None
        if self.trigger == "cross":
            if not bool(row["k_cross_up"]):
                return None
        elif not row["k"] >= row["d"]:
            return None
        if self.require_rsi_flip and not bool(row["rsi_flip_up_recent"]):
            return None

        close = float(row["close"])
        stop = float(close - self.stop_atr * float(row["atr"]))
        risk = close - stop
        if risk <= 0:
            return None
        flashed = ("any green" if self.green_mode == "any"
                   else "first green" if self.entry_window == 1 else "green flash")
        verb = "crossed above" if self.trigger == "cross" else "at/above"
        return Signal(
            action=Action.BUY,
            reason=(
                f"WVF {flashed} + stoch oversold, %K {row['k']:.1f} "
                f"{verb} %D {row['d']:.1f}; RSI {row['rsi']:.1f}"
            ),
            stop_loss=stop,
            take_profit=close + self.rr * risk,
        )