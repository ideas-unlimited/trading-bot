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

from collections import deque

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
    "trendline_channel",
    "trendline_channel_ensemble",
    "channel_breakout",
    "measured_move",
    "optimized_trend_tracker",
    "volume_profile",
    "supply_demand_zones",
    "supply_demand_features",
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
# Chart structure: trendline channels and pattern breakouts
# --------------------------------------------------------------------------
# Ported from the TradingView indicator 'Auto Pattern Detector Targets
# [Ultra]' (pasted by the user 2026-11-27), which draws 20+ chart patterns
# but computes all of them from the same three primitives: a trendline
# through the last two swing pivots, a breakout test against that line
# (ATR margin + optional close/body/volume confirmation), and a measured
# move target equal to the pattern height.  The patterns themselves are
# just named combinations of those primitives, so the primitives are what
# is worth reusing - a strategy can pick them without adopting a chart
# taxonomy.


def trendline_channel(
    df: pd.DataFrame, left: int = 10, right: int = 10
) -> pd.DataFrame:
    '''Upper and lower channel rails from the last two confirmed pivots.

    A swing high/low becomes usable only at its confirmation bar (the
    ``swing_pivots`` lag of ``right`` bars), so the rail at bar *i* is drawn
    with information available at *i*.  Returns ``upper``/``lower`` (price
    on the rail at this bar), ``upper_slope``/``lower_slope`` (price per
    bar, NaN until both rails exist) and ``height`` (upper - lower).

    A converging pair of slopes is a triangle/wedge, parallel is a channel
    or rectangle, diverging is a megaphone - the shapes the source
    indicator names, without the names.
    '''
    if left < 1 or right < 1:
        raise ValueError('left and right must be >= 1')
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    piv = swing_pivots(df['high'], df['low'], left=left, right=right)
    is_h = piv['pivot_high'].to_numpy(dtype=bool)
    is_l = piv['pivot_low'].to_numpy(dtype=bool)
    n = len(df)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    uslope = np.full(n, np.nan)
    lslope = np.full(n, np.nan)
    hist_h: list[tuple[int, float]] = []
    hist_l: list[tuple[int, float]] = []
    for i in range(n):
        # a flag at i means the pivot itself sits at i - right
        if is_h[i]:
            k = i - right
            if k >= 0:
                hist_h.append((k, float(high[k])))
        if is_l[i]:
            k = i - right
            if k >= 0:
                hist_l.append((k, float(low[k])))
        if len(hist_h) >= 2 and len(hist_l) >= 2:
            (hx1, hy1), (hx2, hy2) = hist_h[-2], hist_h[-1]
            (lx1, ly1), (lx2, ly2) = hist_l[-2], hist_l[-1]
            us = (hy2 - hy1) / (hx2 - hx1) if hx2 > hx1 else 0.0
            ls = (ly2 - ly1) / (lx2 - lx1) if lx2 > lx1 else 0.0
            upper[i] = hy2 + us * (i - hx2)
            lower[i] = ly2 + ls * (i - lx2)
            uslope[i] = us
            lslope[i] = ls
    return pd.DataFrame({
        'upper': upper, 'lower': lower,
        'upper_slope': uslope, 'lower_slope': lslope,
        'height': upper - lower,
    }, index=df.index)


def trendline_channel_ensemble(
    df: pd.DataFrame, spans: tuple[int, ...] = (10, 15, 20)
) -> pd.DataFrame:
    '''Median channel across several pivot spans.

    The single-span channel is unstable: moving the span by three bars moves
    the rails by percent, so a gate tuned to one span is fitting noise.  The
    bar-wise median across spans keeps the shape but drops the span as a
    free parameter - there is one number to tune instead of two.  Causal.
    '''
    if not spans:
        raise ValueError('spans must not be empty')
    chans = [trendline_channel(df, left=s, right=s) for s in spans]
    cols = ('upper', 'lower', 'upper_slope', 'lower_slope', 'height')
    out = {c: pd.concat([ch[c] for ch in chans], axis=1).median(axis=1)
           for c in cols}
    return pd.DataFrame(out, index=df.index)

def channel_breakout(
    df: pd.DataFrame,
    channel: pd.DataFrame | None = None,
    *,
    atr_mult: float = 0.5,
    confirm: int = 1,
    atr_window: int = 14,
    left: int = 10,
    right: int = 10,
) -> pd.DataFrame:
    '''Long/short breakouts of the channel rails, ATR-margined.

    ``break_up`` needs ``confirm`` consecutive closes above ``upper`` plus
    ``atr_mult`` ATR (the source indicator's 'breakout threshold', default
    0.3-1.0 ATR).  The bar that trips it is the *signal* bar: the engine
    fills on the next open, as always.  Use ``confirm=2`` to demand a
    second close outside and cut the false breaks.  Causal.
    '''
    if confirm < 1:
        raise ValueError('confirm must be >= 1')
    ch = channel if channel is not None else trendline_channel(df, left, right)
    a = atr(df['high'], df['low'], df['close'], atr_window)
    margin = atr_mult * a
    up_hit = df['close'] > ch['upper'] + margin
    dn_hit = df['close'] < ch['lower'] - margin
    if confirm > 1:
        up_hit = up_hit.rolling(confirm, min_periods=confirm).sum().eq(confirm)
        dn_hit = dn_hit.rolling(confirm, min_periods=confirm).sum().eq(confirm)
    return pd.DataFrame({
        'break_up': up_hit.fillna(False),
        'break_down': dn_hit.fillna(False),
    })


def measured_move(
    df: pd.DataFrame, channel: pd.DataFrame | None = None, left: int = 10, right: int = 10
) -> pd.DataFrame:
    '''Pattern-height targets: the channel's own height added to its rail.

    Every pattern in the source indicator projects the same way - take the
    height of the shape and add it to the breakout side (triangles, flags,
    rectangles, channels, cup-and-handle all share this).  ``target_up`` is
    therefore the first objective for a long entry, not a prediction.
    Causal.
    '''
    ch = channel if channel is not None else trendline_channel(df, left, right)
    height = ch['height'].abs()
    return pd.DataFrame({
        'target_up': ch['upper'] + height,
        'target_down': ch['lower'] - height,
    })

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


# --------------------------------------------------------------------------
# DreamLab: OTT + fixed-range volume profile + supply/demand zones
# Ported 2026-11-28 from the TradingView script
# 'SuperID=OTT+VPSupply/Demand+EMA' (KivancOzbilgic / Anil Ozeksi /
# LonesomeTheBlue; MPL 2.0).  The EMA 20/50 block is already covered by
# bot.indicators.ema and is intentionally not duplicated here.
# --------------------------------------------------------------------------

def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def _wma(series: pd.Series, window: int) -> pd.Series:
    weights = np.arange(1, window + 1, dtype=float)
    return series.rolling(window, min_periods=window).apply(
        lambda x: float(np.dot(x, weights) / weights.sum()), raw=True
    )


def _tsf(series: pd.Series, window: int) -> pd.Series:
    """Pine ta.linreg(src, window, 0) + its one-bar slope (TSF)."""
    if window < 2:
        return series.astype(float)
    x = np.arange(window, dtype=float)
    xbar = x.mean()
    denom = float(((x - xbar) ** 2).sum())

    def _forecast(y: np.ndarray) -> float:
        ybar = y.mean()
        slope = float(((x - xbar) * (y - ybar)).sum() / denom)
        return float(ybar - slope * xbar + slope * window)

    return series.rolling(window, min_periods=window).apply(_forecast, raw=True)


def optimized_trend_tracker(
    df: pd.DataFrame,
    length: int = 2,
    percent: float = 1.4,
    ma_type: str = "VAR",
    source: str = "close",
) -> pd.DataFrame:
    """OTT (Optimized Trend Tracker) trend, trailing stop and signals.

    Ported from the OTT block (KivancOzbilgic, Anil Ozeksi).  ``ma`` is the
    selected moving average, ``long_stop``/``short_stop`` are Pine's ratcheting
    bands, ``trend`` is +1/-1, and ``ott`` is the line the original *plots*
    (it deliberately lags the internal line by two bars, as ``OTT[2]``).
    ``ott_raw`` is the undelayed internal line for research.

    Defaults match the source: length 2, percent 1.4, VAR.  ``ma_type`` is one
    of SMA/EMA/WMA/TMA/VAR/WWMA/ZLEMA/TSF (case-insensitive).  ``source`` is a
    column name (default close).  Signals are crossovers of ``ma`` or close
    against the *plotted* ``ott``; all are causal and actionable on the next
    bar open.  NaN during warmup.
    """
    if length < 1:
        raise ValueError("length must be >= 1")
    if percent < 0:
        raise ValueError("percent must be >= 0")
    src = df[source].astype(float)
    kind = ma_type.upper()
    var = kind == "VAR"
    if var:
        delta = src.diff()
        up = delta.clip(lower=0.0)
        down = (-delta).clip(lower=0.0)
        vud = up.rolling(9, min_periods=9).sum()
        vdd = down.rolling(9, min_periods=9).sum()
        denom = vud + vdd
        cmo = ((vud - vdd) / denom.where(denom > 0)).fillna(0.0)
        alpha = 2.0 / (length + 1.0)
        vals = np.full(len(src), np.nan)
        prev = 0.0
        for i, (x, c) in enumerate(zip(src.to_numpy(float), cmo.to_numpy(float))):
            if not np.isfinite(x) or not np.isfinite(c):
                continue
            a = alpha * abs(float(c))
            prev = a * float(x) + (1.0 - a) * prev
            vals[i] = prev
        ma = pd.Series(vals, index=src.index)
    elif kind == "WWMA":
        alpha = 1.0 / length
        vals = np.full(len(src), np.nan)
        prev = 0.0
        for i, x in enumerate(src.to_numpy(float)):
            if not np.isfinite(x):
                continue
            prev = alpha * float(x) + (1.0 - alpha) * prev
            vals[i] = prev
        ma = pd.Series(vals, index=src.index)
        ma.iloc[: length - 1] = np.nan
    elif kind == "SMA":
        ma = src.rolling(length, min_periods=length).mean()
    elif kind == "EMA":
        ma = ema(src, length)
    elif kind == "WMA":
        ma = _wma(src, length)
    elif kind == "TMA":
        slow = int(np.floor(length / 2.0)) + 1
        fast = int(np.ceil(length / 2.0))
        ma = src.rolling(fast, min_periods=fast).mean().rolling(
            slow, min_periods=slow
        ).mean()
    elif kind == "ZLEMA":
        lag = int(np.floor(length / 2.0))
        z = src + (src - src.shift(lag)) if lag else src
        ma = ema(z, length)
    elif kind == "TSF":
        ma = _tsf(src, length)
    else:
        raise ValueError(
            f"unknown ma_type {ma_type!r}; use SMA/EMA/WMA/TMA/VAR/WWMA/ZLEMA/TSF"
        )

    ma_np = ma.to_numpy(float)
    fark = ma_np * percent * 0.01
    long_stop = np.full(len(df), np.nan)
    short_stop = np.full(len(df), np.nan)
    prev_long = prev_short = np.nan
    for i in range(len(df)):
        if not np.isfinite(ma_np[i]) or not np.isfinite(fark[i]):
            continue
        ls = ma_np[i] - fark[i]
        ss = ma_np[i] + fark[i]
        if np.isfinite(prev_long) and ma_np[i] > prev_long:
            ls = max(ls, prev_long)
        if np.isfinite(prev_short) and ma_np[i] < prev_short:
            ss = min(ss, prev_short)
        long_stop[i], short_stop[i] = ls, ss
        prev_long, prev_short = ls, ss

    trend = np.full(len(df), np.nan)
    last_dir = 1.0
    for i in range(len(df)):
        if not np.isfinite(ma_np[i]):
            continue
        d = last_dir
        lp = long_stop[i - 1] if i > 0 and np.isfinite(long_stop[i - 1]) else long_stop[i]
        sp = short_stop[i - 1] if i > 0 and np.isfinite(short_stop[i - 1]) else short_stop[i]
        if d == -1.0 and ma_np[i] > sp:
            d = 1.0
        elif d == 1.0 and ma_np[i] < lp:
            d = -1.0
        trend[i] = d
        last_dir = d

    mt = np.where(trend == 1.0, long_stop, short_stop)
    ott_raw = np.where(
        ma_np > mt,
        mt * (200.0 + percent) / 200.0,
        mt * (200.0 - percent) / 200.0,
    )
    idx = df.index
    ott = pd.Series(ott_raw, index=idx).shift(2)
    ma_s = pd.Series(ma_np, index=idx)
    close = df["close"].astype(float)
    return pd.DataFrame(
        {
            "ma": ma_s,
            "long_stop": pd.Series(long_stop, index=idx),
            "short_stop": pd.Series(short_stop, index=idx),
            "trend": pd.Series(trend, index=idx),
            "ott": ott,
            "ott_raw": pd.Series(ott_raw, index=idx),
            "support_cross_up": _cross_up(ma_s, ott),
            "support_cross_down": _cross_down(ma_s, ott),
            "price_cross_up": _cross_up(close, ott),
            "price_cross_down": _cross_down(close, ott),
            "color_up": ott > ott.shift(1),
            "color_down": ott < ott.shift(1),
        },
        index=idx,
    )


def volume_profile(
    df: pd.DataFrame,
    window: int = 150,
    rows: int = 24,
    value_area: float = 0.70,
) -> pd.DataFrame:
    """Fixed-range volume profile: POC and value area, computed as-of each bar.

    Ported from the Volume Profile / Fixed Range block (LonesomeTheBlue).  At
    every bar it builds a profile over the prior ``window`` bars using Pine's
    body/wick volume split: body volume goes to the bar's direction, each
    wick's volume is split half to up and half to down, and volume is
    distributed across price rows by overlap.  ``value_area`` is the fraction
    of total volume (0-1) that the value area must cover.

    Returns one row per input bar with ``poc``, ``vah``, ``val``,
    ``in_value_area``, ``poc_volume``, ``value_area_volume``, ``total_volume``,
    ``poc_buy_share`` and ``value_area_buy_share``.  A row is NaN until
    ``window`` bars exist.  Causal: a row at bar *i* uses bars <= *i* only.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    if rows < 1:
        raise ValueError("rows must be >= 1")
    if not 0.0 < value_area <= 1.0:
        raise ValueError("value_area must be in (0, 1]")
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    n = len(df)
    out = {
        "poc": np.full(n, np.nan),
        "vah": np.full(n, np.nan),
        "val": np.full(n, np.nan),
        "poc_volume": np.full(n, np.nan),
        "value_area_volume": np.full(n, np.nan),
        "total_volume": np.full(n, np.nan),
        "poc_buy_share": np.full(n, np.nan),
        "value_area_buy_share": np.full(n, np.nan),
    }
    in_va = np.zeros(n, dtype=bool)
    for i in range(window - 1, n):
        sl = slice(i - window + 1, i + 1)
        oo, hh, ll, cc, vv = o[sl], h[sl], l[sl], c[sl], v[sl]
        if not (np.isfinite(oo).all() and np.isfinite(hh).all()
                and np.isfinite(ll).all() and np.isfinite(cc).all()
                and np.isfinite(vv).all()):
            continue
        bot = float(ll.min())
        top = float(hh.max())
        if not top > bot:
            continue
        step = (top - bot) / rows
        levels = bot + step * np.arange(rows + 1, dtype=float)
        bin_lo = levels[:-1][None, :]
        bin_hi = levels[1:][None, :]

        body_top = np.maximum(oo, cc)
        body_bot = np.minimum(oo, cc)
        body = body_top - body_bot
        top_wick = hh - body_top
        bot_wick = body_bot - ll
        denom = 2.0 * top_wick + 2.0 * bot_wick + body
        body_vol = np.divide(
            body * vv, denom, out=np.zeros_like(body), where=denom != 0
        )
        top_vol = np.divide(
            2.0 * top_wick * vv, denom, out=np.zeros_like(body), where=denom != 0
        )
        bot_vol = np.divide(
            2.0 * bot_wick * vv, denom, out=np.zeros_like(body), where=denom != 0
        )

        def _overlap(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
            return np.clip(
                np.minimum(hi[:, None], bin_hi) - np.maximum(lo[:, None], bin_lo),
                0.0,
                None,
            )

        body_frac = np.divide(
            _overlap(body_bot, body_top), body[:, None],
            out=np.zeros((len(body), rows)), where=body[:, None] != 0,
        )
        top_frac = np.divide(
            _overlap(body_top, hh), top_wick[:, None],
            out=np.zeros((len(body), rows)), where=top_wick[:, None] != 0,
        )
        bot_frac = np.divide(
            _overlap(ll, body_bot), bot_wick[:, None],
            out=np.zeros((len(body), rows)), where=bot_wick[:, None] != 0,
        )
        body_alloc = body_frac * body_vol[:, None]
        wick_alloc = (top_frac * (top_vol / 2.0)[:, None]
                      + bot_frac * (bot_vol / 2.0)[:, None])
        green = cc >= oo
        up = np.where(green[:, None], body_alloc, 0.0) + wick_alloc
        down = np.where(green[:, None], 0.0, body_alloc) + wick_alloc
        total = up.sum(axis=0) + down.sum(axis=0)
        total_sum = float(total.sum())
        if total_sum <= 0:
            continue

        poc_idx = int(np.argmax(total))
        target = total_sum * value_area
        va_total = float(total[poc_idx])
        va_up = va_down = poc_idx
        for _ in range(rows - 1):
            if va_total >= target:
                break
            upper = float(total[va_up + 1]) if va_up + 1 < rows else 0.0
            lower = float(total[va_down - 1]) if va_down > 0 else 0.0
            if upper <= 0.0 and lower <= 0.0:
                break
            if upper >= lower:
                va_total += upper
                va_up += 1
            else:
                va_total += lower
                va_down -= 1

        vah = float(levels[va_up + 1])
        val = float(levels[va_down])
        poc = float((levels[poc_idx] + levels[poc_idx + 1]) / 2.0)
        out["poc"][i] = poc
        out["vah"][i] = vah
        out["val"][i] = val
        out["poc_volume"][i] = float(total[poc_idx])
        out["value_area_volume"][i] = va_total
        out["total_volume"][i] = total_sum
        if total[poc_idx] > 0:
            out["poc_buy_share"][i] = float(up[:, poc_idx].sum() / total[poc_idx])
        va_slice = slice(va_down, va_up + 1)
        va_sum = float(total[va_slice].sum())
        if va_sum > 0:
            out["value_area_buy_share"][i] = float(up[:, va_slice].sum() / va_sum)
        in_va[i] = val <= c[i] <= vah

    out["in_value_area"] = in_va
    return pd.DataFrame(out, index=df.index)


def _supply_demand_walk(
    df: pd.DataFrame,
    swing_length: int,
    atr_window: int,
    box_width: float,
    history: int,
    overlap_atr: float,
):
    n = len(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    close_np = close.to_numpy(float)
    a = atr(high, low, close, atr_window).to_numpy(float)
    piv = swing_pivots(high, low, swing_length, swing_length)
    hp = piv["pivot_high"].to_numpy(bool)
    lp = piv["pivot_low"].to_numpy(bool)
    supply: deque[dict] = deque()
    demand: deque[dict] = deque()
    zones: list[dict] = []

    def _try_zone(kind: str, pivot_value: float, pivot_pos: int,
                  active: deque, i: int) -> None:
        atr_value = a[i]
        if not np.isfinite(atr_value) or atr_value <= 0:
            return
        buffer = atr_value * (box_width / 10.0)
        if kind == "supply":
            top = float(pivot_value)
            bottom = top - buffer
        else:
            bottom = float(pivot_value)
            top = bottom + buffer
        poi = (top + bottom) / 2.0
        if not top > bottom:
            return
        if any(abs(poi - z["poi"]) <= overlap_atr * atr_value for z in active):
            return
        if len(active) >= history:
            old = active.pop()
            old["removed_at"] = i
            old["removed_reason"] = "history"
        z = {
            "kind": kind,
            "top": top,
            "bottom": bottom,
            "poi": poi,
            "pivot_at": pivot_pos,
            "confirmed_at": i,
            "broken_at": None,
            "removed_at": None,
            "removed_reason": None,
        }
        active.appendleft(z)
        zones.append(z)

    feat = {
        "supply_top": np.full(n, np.nan),
        "supply_bottom": np.full(n, np.nan),
        "supply_poi": np.full(n, np.nan),
        "supply_dist_atr": np.full(n, np.nan),
        "supply_age": np.full(n, np.nan),
        "demand_top": np.full(n, np.nan),
        "demand_bottom": np.full(n, np.nan),
        "demand_poi": np.full(n, np.nan),
        "demand_dist_atr": np.full(n, np.nan),
        "demand_age": np.full(n, np.nan),
    }
    for name in ("in_supply", "in_demand", "supply_break", "demand_break"):
        feat[name] = np.zeros(n, dtype=bool)
    for name in ("active_supply", "active_demand"):
        feat[name] = np.zeros(n, dtype=int)

    for i in range(n):
        if i >= swing_length:
            pivot_pos = i - swing_length
            if hp[i]:
                _try_zone("supply", high.iloc[pivot_pos], pivot_pos, supply, i)
            elif lp[i]:
                _try_zone("demand", low.iloc[pivot_pos], pivot_pos, demand, i)

        for z in list(supply):
            if close_np[i] >= z["top"]:
                z["broken_at"] = i
                z["removed_at"] = i
                z["removed_reason"] = "broken"
                feat["supply_break"][i] = True
                supply.remove(z)
        for z in list(demand):
            if close_np[i] <= z["bottom"]:
                z["broken_at"] = i
                z["removed_at"] = i
                z["removed_reason"] = "broken"
                feat["demand_break"][i] = True
                demand.remove(z)

        def _nearest(active: deque):
            if not active:
                return None
            return min(active, key=lambda z: abs(close_np[i] - z["poi"]))

        sz = _nearest(supply)
        dz = _nearest(demand)
        atr_value = a[i]
        if sz is not None:
            feat["supply_top"][i] = sz["top"]
            feat["supply_bottom"][i] = sz["bottom"]
            feat["supply_poi"][i] = sz["poi"]
            if np.isfinite(atr_value) and atr_value > 0:
                feat["supply_dist_atr"][i] = abs(close_np[i] - sz["poi"]) / atr_value
            feat["supply_age"][i] = i - sz["confirmed_at"]
            feat["in_supply"][i] = sz["bottom"] <= close_np[i] <= sz["top"]
        if dz is not None:
            feat["demand_top"][i] = dz["top"]
            feat["demand_bottom"][i] = dz["bottom"]
            feat["demand_poi"][i] = dz["poi"]
            if np.isfinite(atr_value) and atr_value > 0:
                feat["demand_dist_atr"][i] = abs(close_np[i] - dz["poi"]) / atr_value
            feat["demand_age"][i] = i - dz["confirmed_at"]
            feat["in_demand"][i] = dz["bottom"] <= close_np[i] <= dz["top"]
        feat["active_supply"][i] = len(supply)
        feat["active_demand"][i] = len(demand)

    if zones:
        records = []
        for z in zones:
            end = z["removed_at"] if z["removed_at"] is not None else n - 1
            records.append({
                "kind": z["kind"],
                "top": z["top"],
                "bottom": z["bottom"],
                "poi": z["poi"],
                "pivot_at": df.index[z["pivot_at"]],
                "confirmed_at": df.index[z["confirmed_at"]],
                "broken_at": (df.index[z["broken_at"]]
                               if z["broken_at"] is not None else pd.NaT),
                "removed_at": (df.index[z["removed_at"]]
                                if z["removed_at"] is not None else pd.NaT),
                "removed_reason": z["removed_reason"],
                "bars_active": end - z["confirmed_at"],
            })
        ledger = pd.DataFrame(records)
    else:
        ledger = pd.DataFrame(columns=[
            "kind", "top", "bottom", "poi", "pivot_at", "confirmed_at",
            "broken_at", "removed_at", "removed_reason", "bars_active",
        ])
    return ledger, pd.DataFrame(feat, index=df.index)


def supply_demand_zones(
    df: pd.DataFrame,
    swing_length: int = 10,
    atr_window: int = 50,
    box_width: float = 2.5,
    history: int = 20,
    overlap_atr: float = 2.0,
) -> pd.DataFrame:
    """Supply/demand zone ledger built from confirmed swing pivots.

    Ported from the Supply/Demand block: a swing high creates a supply zone
    from the pivot high down by ``box_width/10 * ATR``; a swing low creates the
    mirror demand zone.  Zones whose midpoints are within ``overlap_atr`` ATR
    of an existing same-side zone are skipped.  A zone is removed when close
    breaks its far edge (supply: close >= top; demand: close <= bottom), which
    is the source's BOS event.  Only ``history`` zones per side are kept.

    The pivot is confirmed ``swing_length`` bars after it forms; ``confirmed_at``
    is the first bar at which the zone may be used.  Never use the zone before
    that timestamp (this is the port's no-lookahead guarantee).  Returns a
    ledger with kind/top/bottom/poi/pivot_at/confirmed_at/broken_at/removed_at/
    removed_reason/bars_active.  See ``supply_demand_features`` for per-bar
    values ready for a strategy.
    """
    if swing_length < 1:
        raise ValueError("swing_length must be >= 1")
    if history < 1:
        raise ValueError("history must be >= 1")
    if box_width <= 0:
        raise ValueError("box_width must be > 0")
    ledger, _ = _supply_demand_walk(
        df, swing_length, atr_window, box_width, history, overlap_atr
    )
    return ledger


def supply_demand_features(
    df: pd.DataFrame,
    swing_length: int = 10,
    atr_window: int = 50,
    box_width: float = 2.5,
    history: int = 20,
    overlap_atr: float = 2.0,
) -> pd.DataFrame:
    """Per-bar supply/demand state from confirmed zones (causal).

    Returns the nearest active zone on each side plus distance to its POI in
    ATR units, age in bars, whether close is inside the zone, BOS flags and
    active-zone counts.  A zone first appears at its pivot confirmation bar,
    so a strategy can act on it at the next open.  NaN/0 during warmup.
    """
    if swing_length < 1:
        raise ValueError("swing_length must be >= 1")
    if history < 1:
        raise ValueError("history must be >= 1")
    if box_width <= 0:
        raise ValueError("box_width must be > 0")
    _, features = _supply_demand_walk(
        df, swing_length, atr_window, box_width, history, overlap_atr
    )
    return features
