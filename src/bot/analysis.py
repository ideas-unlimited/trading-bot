"""Reusable analysis toolkit — the "beyond simple tools" layer.

Every function here is a *method for extracting information* from OHLCV
data, kept separate from any single strategy so future strategies can
compose them freely.

Conventions (follow these when porting a pasted indicator):

1. Input: ``df`` is an OHLCV DataFrame with lowercase columns
   (``open, high, low, close, volume``) indexed by bar timestamp, or plain
   ``pd.Series`` for single-series stats.
2. Output: ``pd.Series`` (or tuple of Series) aligned to the input index;
   ``NaN`` where there is not enough history — never silently zero-filled.
3. **Causal only.** A value at bar *i* may use bars ``<= i`` only. Where a
   pattern needs ``right`` future bars to confirm (e.g. fractal pivots),
   the signal must appear at the confirmation bar, not at the pattern bar.
   Every function here passes the prefix-stability test in
   ``tests/test_analysis.py``.
4. Parameters are explicit keyword args with sane defaults; document units
   (bars, multiples of ATR, percentiles).
5. Ported indicators get a one-line provenance note (source + date) and a
   test in ``tests/test_analysis.py``.

Existing "simple" indicators (sma/ema/rsi/atr/bollinger) stay in
``bot.indicators``; everything else belongs here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, ema, rsi

__all__ = [
    "efficiency_ratio",
    "adx",
    "variance_ratio_hurst",
    "vol_regime",
    "zscore",
    "rolling_skew",
    "rolling_kurtosis",
    "rolling_autocorr",
    "drawdown",
    "swing_pivots",
    "liquidity_levels",
    "sweep_reclaim",
    "level_touches",
    "vwap_rolling",
    "volume_zscore",
    "obv_slope",
    "directional_volume",
    "wick_ratio",
    "range_expansion",
    "rsi_divergence",
    "supertrend",
    "range_filter",
    "choppiness_index",
    "damiani_volatmeter",
    "schaff_trend_cycle",
    "williams_vix_fix",
    "stochastic",
]


# --------------------------------------------------------------------------
# Trend quality / regime
# --------------------------------------------------------------------------

def efficiency_ratio(close: pd.Series, window: int = 10) -> pd.Series:
    """Kaufman efficiency ratio: net move / sum of absolute moves, in [0, 1].

    High values = clean directional drift (trend-friendly); near zero =
    chop. Causal.
    """
    change = close.diff().abs().rolling(window, min_periods=window).sum()
    net = close.diff(window).abs()
    return net / change.where(change > 0)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ADX with +DI / -DI. Returns (adx, plus_di, minus_di)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_w = tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).where(lambda x: x != 0)
    adx_out = dx.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    return adx_out, plus_di, minus_di


def variance_ratio_hurst(close: pd.Series, window: int = 128, horizon: int = 8) -> pd.Series:
    """Rolling Hurst exponent estimate via the variance-ratio method.

    H ≈ log(Var(r_horizon) / Var(r_1)) / (2 * log(horizon)).  H > 0.5 ⇒
    persistence/trending, H < 0.5 ⇒ mean reversion.  Cheap but noisy;
    treat as a regime filter, not a precise number.  Causal.
    """
    lr = np.log(close / close.shift(1))
    var1 = lr.rolling(window, min_periods=window).var(ddof=0)
    varn = lr.rolling(horizon).sum().rolling(window, min_periods=window).var(ddof=0)
    h = np.log(varn / var1) / (2.0 * np.log(horizon))
    return h.replace([np.inf, -np.inf], np.nan)


def vol_regime(close: pd.Series, window: int = 60) -> pd.DataFrame:
    """Realized volatility and its rolling percentile.

    Returns DataFrame with ``vol`` (annualized log-return std over
    ``window`` bars) and ``vol_pct`` (percentile of vol over a 4x window —
    i.e. how extreme current volatility is vs its own recent history).
    Causal.
    """
    lr = np.log(close / close.shift(1))
    vol = lr.rolling(window, min_periods=window).std(ddof=0) * np.sqrt(365 * 6)
    vol_pct = vol.rolling(window * 4, min_periods=window).apply(
        lambda x: (x[-1] > x).mean() * 100, raw=True
    )
    return pd.DataFrame({"vol": vol, "vol_pct": vol_pct})


# --------------------------------------------------------------------------
# Distribution / statistics of returns
# --------------------------------------------------------------------------

def zscore(series: pd.Series, window: int = 100) -> pd.Series:
    """Rolling z-score of a series vs its own window. Causal."""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    return (series - mean) / std.where(std > 0)


def rolling_skew(close: pd.Series, window: int = 60) -> pd.Series:
    """Rolling skewness of log returns. Fat left tail ⇒ negative. Causal."""
    lr = np.log(close / close.shift(1))
    return lr.rolling(window, min_periods=window).skew()


def rolling_kurtosis(close: pd.Series, window: int = 60) -> pd.Series:
    """Rolling excess kurtosis of log returns (tail heaviness). Causal."""
    lr = np.log(close / close.shift(1))
    return lr.rolling(window, min_periods=window).kurt()


def rolling_autocorr(close: pd.Series, window: int = 60, lag: int = 1) -> pd.Series:
    """Rolling autocorrelation of log returns at ``lag``.

    Positive ⇒ momentum-dominated regime; negative ⇒ mean-reverting.
    Causal.
    """
    lr = np.log(close / close.shift(1))
    return lr.rolling(window, min_periods=window).corr(lr.shift(lag))


def drawdown(close: pd.Series) -> pd.Series:
    """Running drawdown from the all-time (in-sample) high, ≤ 0. Causal."""
    return close / close.cummax() - 1.0


# --------------------------------------------------------------------------
# Market structure / liquidity
# --------------------------------------------------------------------------

def swing_pivots(
    high: pd.Series, low: pd.Series, left: int = 3, right: int = 3
) -> pd.DataFrame:
    """Fractal swing pivots.

    A bar is a pivot high if its high is the max of ``left`` bars before and
    ``right`` bars after; mirror for pivot low.  **Confirmation lag:** a
    pivot at bar *i* becomes known at bar *i + right* — the output marks
    the pivot at the *confirmation* index so the signal is causal (never
    act on a pivot before it confirms).
    """
    ph = high.rolling(left + right + 1, center=True, min_periods=left + right + 1).max()
    pl = low.rolling(left + right + 1, center=True, min_periods=left + right + 1).min()
    confirmed_h = (high == ph).shift(right, fill_value=False)
    confirmed_l = (low == pl).shift(right, fill_value=False)
    return pd.DataFrame({"pivot_high": confirmed_h, "pivot_low": confirmed_l})


def liquidity_levels(
    high: pd.Series, low: pd.Series, window: int = 20
) -> pd.DataFrame:
    """Resting-liquidity proxy levels from prior closed bars.

    ``sell_side``: the lowest low of the prior ``window`` closed bars (stops
    parked below) — the level a downside sweep would run.
    ``buy_side``: highest high of the prior window (stops parked above).
    Computed with ``shift(1)`` so bar *i* uses bars ``< i`` only.  Causal.
    """
    return pd.DataFrame(
        {
            "sell_side": low.rolling(window, min_periods=window).min().shift(1),
            "buy_side": high.rolling(window, min_periods=window).max().shift(1),
        }
    )


def sweep_reclaim(
    df: pd.DataFrame, window: int = 20, reclaim_frac: float = 0.0
) -> pd.DataFrame:
    """Liquidity-sweep detection (stop-hunt signature).

    ``sell_sweep``: bar's low wicks below the prior ``window``-bar low (the
    pool gets run) but the close is back above the level
    (``>= sell_side + reclaim_frac * (high - low)`` to demand a stronger
    reclaim).  ``buy_sweep``: mirror above.  Signal fires on the sweeping
    bar itself — the entry decision happens on the *next* bar, consistent
    with the engine.  Causal.
    """
    lv = liquidity_levels(df["high"], df["low"], window)
    rng = (df["high"] - df["low"]).where(lambda x: x > 0)
    sell_sweep = (df["low"] < lv["sell_side"]) & (
        df["close"] >= lv["sell_side"] + reclaim_frac * rng
    )
    buy_sweep = (df["high"] > lv["buy_side"]) & (
        df["close"] <= lv["buy_side"] - reclaim_frac * rng
    )
    return pd.DataFrame({"sell_sweep": sell_sweep, "buy_sweep": buy_sweep})


def level_touches(
    high: pd.Series, low: pd.Series, window: int = 20, tol_atr: float = 0.25,
    atr_window: int = 14, lookback: int = 100,
) -> pd.DataFrame:
    """How often price revisited the window extremes recently.

    Many touches of the same level within ``tol_atr`` ATR ⇒ equal
    highs/lows, i.e. a visible liquidity pool attracting stops.  Returns
    ``touch_high`` / ``touch_low`` counts over the last ``lookback`` bars.
    Causal.
    """
    a = atr(high, low, high, atr_window)
    bh = high.rolling(window, min_periods=window).max().shift(1)
    bl = low.rolling(window, min_periods=window).min().shift(1)
    th = pd.Series(0.0, index=high.index)
    tl = pd.Series(0.0, index=high.index)
    near_h = (high - bh).abs() <= tol_atr * a
    near_l = (low - bl).abs() <= tol_atr * a
    th = near_h.rolling(lookback, min_periods=lookback).sum()
    tl = near_l.rolling(lookback, min_periods=lookback).sum()
    return pd.DataFrame({"touch_high": th, "touch_low": tl})


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------

def vwap_rolling(df: pd.DataFrame, window: int = 24) -> pd.Series:
    """Rolling volume-weighted average price (session-agnostic). Causal."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    num = (tp * df["volume"]).rolling(window, min_periods=window).sum()
    den = df["volume"].rolling(window, min_periods=window).sum()
    return num / den.where(den > 0)


def volume_zscore(df: pd.DataFrame, window: int = 100) -> pd.Series:
    """Volume vs its own rolling distribution — spike detection. Causal."""
    return zscore(df["volume"].astype(float), window)


def obv_slope(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """On-balance volume, normalized slope (z-scored). Positive = accumulation."""
    direction = np.sign(df["close"].diff()).fillna(0.0)
    obv = (direction * df["volume"].astype(float)).cumsum()
    slope = obv.diff(window)
    scale = df["volume"].astype(float).rolling(window, min_periods=window).sum()
    return (slope / scale.where(scale > 0)).clip(-5, 5)


def directional_volume(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Up-bar minus down-bar volume share over the window, in [-1, 1].

    +1 = all volume on up closes (aggressive buying), -1 = selling.
    Causal.
    """
    vol = df["volume"].astype(float)
    up = vol.where(df["close"] > df["open"], 0.0).rolling(window, min_periods=window).sum()
    down = vol.where(df["close"] < df["open"], 0.0).rolling(window, min_periods=window).sum()
    total = (up + down).where(lambda x: x > 0)
    return (up - down) / total


# --------------------------------------------------------------------------
# Candle microstructure
# --------------------------------------------------------------------------

def wick_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Upper/lower wick as a fraction of the bar range, in [0, 1].

    Long lower wicks on sweep bars are rejection evidence.  Range 0 bars
    (doji at open=high=low) → 0.  Causal.
    """
    rng = (df["high"] - df["low"]).where(lambda x: x > 0, 0.0)
    upper = (df["high"] - df[["open", "close"]].max(axis=1)) / rng.where(rng > 0)
    lower = (df[["open", "close"]].min(axis=1) - df["low"]) / rng.where(rng > 0)
    return pd.DataFrame(
        {"upper_wick": upper.fillna(0.0), "lower_wick": lower.fillna(0.0)}
    )


def range_expansion(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """Range-contraction / expansion flags.

    ``nr7``: narrowest range of the last 7 bars (volatility compression
    often precedes expansion).  ``inside``: bar inside the prior bar's
    range.  ``expansion``: range > 1.5x the window median.  Causal.
    """
    rng = df["high"] - df["low"]
    nr7 = rng == rng.rolling(7, min_periods=7).min()
    inside = (df["high"] <= df["high"].shift(1)) & (df["low"] >= df["low"].shift(1))
    med = rng.rolling(window, min_periods=window).median()
    expansion = rng > 1.5 * med.where(med > 0)
    return pd.DataFrame({"nr7": nr7, "inside": inside, "expansion": expansion})


# --------------------------------------------------------------------------
# Divergences
# --------------------------------------------------------------------------

def rsi_divergence(
    high: pd.Series, low: pd.Series, close: pd.Series,
    rsi_window: int = 14, left: int = 3, right: int = 3,
    lookback: int = 30,
) -> pd.DataFrame:
    """Regular RSI divergences confirmed at the second pivot.

    ``bullish``: price makes a lower low while RSI makes a higher low
    (momentum turning up).  ``bearish``: price higher high, RSI lower
    high.  Both pivot pairs use ``swing_pivots`` so confirmation lags
    ``right`` bars — causal by construction.
    """
    r = rsi(close, rsi_window)
    piv = swing_pivots(high, low, left, right)
    out = pd.DataFrame(False, index=close.index, columns=["bullish", "bearish"])
    ph_idx = close.index[piv["pivot_high"]]
    pl_idx = close.index[piv["pivot_low"]]
    for idx in ph_idx[1:]:
        prev = [i for i in ph_idx if i < idx][-1:]
        if not prev:
            continue
        p0 = close.index.get_loc(prev[0])
        p1 = close.index.get_loc(idx)
        if p1 - p0 <= lookback and close.iloc[p1] > close.iloc[p0] and r.iloc[p1] < r.iloc[p0]:
            out.iloc[p1, out.columns.get_loc("bearish")] = True
    for idx in pl_idx[1:]:
        prev = [i for i in pl_idx if i < idx][-1:]
        if not prev:
            continue
        p0 = close.index.get_loc(prev[0])
        p1 = close.index.get_loc(idx)
        if p1 - p0 <= lookback and close.iloc[p1] < close.iloc[p0] and r.iloc[p1] > r.iloc[p0]:
            out.iloc[p1, out.columns.get_loc("bullish")] = True
    return out


# --------------------------------------------------------------------------
# Ported Pine indicators (user-pasted "DIY Custom Strategy Builder [ZP]",
# ported 2026-11-26).  Each keeps the original's math; see the docstrings for
# the source defaults.  All are causal.
# --------------------------------------------------------------------------

def supertrend(
    df: pd.DataFrame, atr_window: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """SuperTrend trailing band and regime state.

    Ported from the Pine 'Supertrend' block (ATR period 10, multiplier 3.0,
    source hl2).  'line' is the trailing stop level for the *current* state:
    below price in an uptrend, above it in a downtrend.  'trend' is +1/-1 and
    flips when the close crosses the opposite band.  Both bands ratchet (they
    only tighten while the trend holds), so a stop taken from 'line' never
    loosens.  Causal.
    """
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    a = atr(df["high"], df["low"], df["close"], atr_window).to_numpy(float)
    n = len(close)
    hl2 = (high + low) / 2.0
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    trend = np.full(n, np.nan)
    prev_up = prev_dn = np.nan
    prev_trend = 1.0
    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        up = hl2[i] - multiplier * a[i]
        dn = hl2[i] + multiplier * a[i]
        if i > 0 and np.isfinite(prev_up) and close[i - 1] > prev_up:
            up = max(up, prev_up)
        if i > 0 and np.isfinite(prev_dn) and close[i - 1] < prev_dn:
            dn = min(dn, prev_dn)
        if prev_trend == -1.0 and np.isfinite(prev_dn) and close[i] > prev_dn:
            t = 1.0
        elif prev_trend == 1.0 and np.isfinite(prev_up) and close[i] < prev_up:
            t = -1.0
        else:
            t = prev_trend
        upper[i], lower[i], trend[i] = up, dn, t
        prev_up, prev_dn, prev_trend = up, dn, t
    line = np.where(trend == 1.0, upper, lower)
    flip = np.zeros(n, dtype=bool)
    flip[1:] = np.nan_to_num(trend[1:]) != np.nan_to_num(trend[:-1])
    return pd.DataFrame({"line": line, "trend": trend, "flip": flip}, index=df.index)


def range_filter(
    df: pd.DataFrame, period: int = 100, multiplier: float = 3.0
) -> pd.DataFrame:
    """Range Filter (Type 1 "Default" in the Pine script).

    The filter is a clamped walk: a smoothed average range (an EMA of the
    average absolute change, itself EMA-smoothed over 2*period-1 bars, times
    'multiplier') bounds how far the filter can move per bar.  Price above a
    rising filter is an uptrend and vice versa.  'state' is +1/-1/0 (0 while
    the filter is flat, which is how the original refuses to call a
    direction).  Causal.
    """
    src = df["close"].astype(float)
    wper = max(2, period * 2 - 1)
    avrng = ema(src.diff().abs(), period)
    rng = ema(avrng, wper) * multiplier
    x = src.to_numpy(float)
    r = rng.to_numpy(float)
    n = len(x)
    filt = np.full(n, np.nan)
    prev = np.nan
    for i in range(n):
        if not np.isfinite(r[i]):
            continue
        if not np.isfinite(prev):
            prev = x[i]
        elif x[i] > prev:
            prev = prev if x[i] - r[i] < prev else x[i] - r[i]
        else:
            prev = prev if x[i] + r[i] > prev else x[i] + r[i]
        filt[i] = prev
    filt_s = pd.Series(filt, index=df.index)
    chg = filt_s.diff()
    state = pd.Series(0.0, index=df.index)
    state[(src > filt_s) & (chg > 0)] = 1.0
    state[(src < filt_s) & (chg < 0)] = -1.0
    state[filt_s.isna()] = float("nan")
    return pd.DataFrame({"filt": filt_s, "state": state}, index=df.index)


def choppiness_index(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Choppiness Index, 0-100 (Pine 'Choppiness Index' block).

    100 * log10(sum(TR, n) / (HH - LL)) / log10(n).  High values (>61.8 by
    convention) mean price is covering distance without going anywhere - a
    trend-following rule's worst regime.  Causal.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    sum_tr = tr.rolling(window, min_periods=window).sum()
    span = (high.rolling(window, min_periods=window).max()
            - low.rolling(window, min_periods=window).min())
    ratio = sum_tr / span.where(span > 0)
    return (100.0 * np.log10(ratio) / np.log10(window)).where(ratio > 0)


def damiani_volatmeter(
    df: pd.DataFrame,
    vis_atr: int = 13,
    sed_atr: int = 40,
    vis_std: int = 20,
    sed_std: int = 100,
    threshold: float = 1.4,
    lag_suppressor: bool = True,
) -> pd.DataFrame:
    """Damiani Volatmeter: is volatility expanding above its baseline?

    Ported from the Pine 'Damiani Volatmeter' block.  'vol' is the short/long
    ATR ratio (with the original's 0.5 lag-suppressor recursion), 'cut' is
    the self-adjusting threshold (threshold - stdev(20)/stdev(100)), and
    'expanding' is vol > cut - the original's "green" state.  The author uses
    it as a filter: green = volatility is awake.  Causal.
    """
    close = df["close"].astype(float)
    short = atr(df["high"], df["low"], df["close"], vis_atr)
    long_ = atr(df["high"], df["low"], df["close"], sed_atr)
    base = (short / long_.where(long_ > 0)).to_numpy(float)
    n = len(base)
    vol = np.full(n, np.nan)
    prev1 = prev3 = 0.0
    for i in range(n):
        if not np.isfinite(base[i]):
            continue
        v = base[i] + 0.5 * (prev1 - prev3) if lag_suppressor else base[i]
        vol[i] = v
        prev3 = prev1
        prev1 = v
    vol_s = pd.Series(vol, index=df.index)
    anti = (close.rolling(vis_std, min_periods=vis_std).std(ddof=0)
            / close.rolling(sed_std, min_periods=sed_std).std(ddof=0))
    cut = threshold - anti
    return pd.DataFrame({"vol": vol_s, "cut": cut, "expanding": vol_s > cut}, index=df.index)


def schaff_trend_cycle(
    close: pd.Series,
    fast: int = 23,
    slow: int = 50,
    cycle: int = 10,
    d1: int = 3,
    d2: int = 3,
) -> pd.Series:
    """Schaff Trend Cycle, 0-100 (Pine 'Schaff Trend Cycle (STC)' block).

    MACD stochastically normalized over 'cycle' bars, EMA-smoothed, then
    normalized and smoothed again.  The original calls >=75 up and <=25 down;
    being a cycle it turns earlier than an SMA cross.  Causal.
    """
    macd = ema(close.astype(float), fast) - ema(close.astype(float), slow)

    def _stoch(s: pd.Series) -> pd.Series:
        lo = s.rolling(cycle, min_periods=cycle).min()
        hi = s.rolling(cycle, min_periods=cycle).max()
        return 100.0 * (s - lo) / (hi - lo).where(lambda x: x > 0)

    k = _stoch(macd).ffill().fillna(0.0)
    d = ema(k, d1).ffill().fillna(0.0)
    kd = _stoch(d).ffill().fillna(0.0)
    return ema(kd, d2).fillna(0.0).clip(0.0, 100.0)


# --------------------------------------------------------------------------
# Pasted-indicator ports
# --------------------------------------------------------------------------

def williams_vix_fix(
    df: pd.DataFrame,
    lookback_high: int = 22,
    band_length: int = 20,
    band_mult: float = 2.0,
    percentile_window: int = 50,
    percentile_high: float = 0.85,
) -> pd.DataFrame:
    """Williams Vix Fix (CM_Williams_Vix_Fix, LazyBear / TheCoder, TradingView).

    Ported 2026-11-27 for the William_Vix_Fix_Stoch strategy.  ``wvf`` measures
    panic as the percentage distance of today's low from the highest close of
    the past ``lookback_high`` bars.  A "green bar" (crash signal) prints when
    wvf exceeds its Bollinger band (``mid + band_mult * stdev``) or the
    ``percentile_high`` fraction of its ``percentile_window`` maximum —
    matching the original's colour logic ``wvf >= upperBand or wvf >= rangeHigh``.

    All inputs are past-only (rolling windows over bars <= i), so the output
    is causal.  Returns columns: ``wvf``, ``mid_line``, ``upper_band``,
    ``range_high``, ``green`` (bool, True = bar is green / setup ready).
    """
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    hh = close.rolling(lookback_high, min_periods=lookback_high).max()
    wvf = 100.0 * (hh - low) / hh.where(hh > 0)
    mid_line = wvf.rolling(band_length, min_periods=band_length).mean()
    band_sd = wvf.rolling(band_length, min_periods=band_length).std(ddof=0)
    upper_band = mid_line + band_mult * band_sd
    range_high = (
        wvf.rolling(percentile_window, min_periods=percentile_window).max()
        * percentile_high
    )
    green = (wvf >= upper_band) | (wvf >= range_high)
    green &= wvf.notna() & upper_band.notna() & range_high.notna()
    return pd.DataFrame(
        {
            "wvf": wvf,
            "mid_line": mid_line,
            "upper_band": upper_band,
            "range_high": range_high,
            "green": green,
        },
        index=df.index,
    )


def stochastic(
    df: pd.DataFrame,
    k_length: int = 14,
    smooth_k: int = 1,
    smooth_d: int = 3,
) -> pd.DataFrame:
    """Stochastic oscillator (TradingView built-in 'Stochastic', Pine v6).

    Ported 2026-11-27 for the William_Vix_Fix_Stoch strategy.  Raw %K is
    ``100 * (close - lowest(low, k)) / (highest(high, k) - lowest(low, k))``
    (the Pine ``ta.stoch(close, high, low, periodK)`` definition), then %K is
    SMA-smoothed by ``smooth_k`` and %D is an SMA of %K over ``smooth_d``.
    The script this was ported from uses smoothK=1 (raw %K, blue line) and
    smoothD=3 (red line).  Causal: rolling windows over bars <= i only.
    Returns columns ``k`` and ``d`` in [0, 100] (NaN during warmup).
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    lo = low.rolling(k_length, min_periods=k_length).min()
    hi = high.rolling(k_length, min_periods=k_length).max()
    raw_k = 100.0 * (close - lo) / (hi - lo).where(lambda x: x > 0)
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(smooth_d, min_periods=smooth_d).mean()
    return pd.DataFrame({"k": k, "d": d}, index=df.index)
