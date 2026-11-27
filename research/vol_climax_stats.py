"""Does the volume-climax rule carry information?  Permutation + cost tests.

Two questions the raw backtest cannot answer:

  1. Is the edge distinguishable from luck?  Shuffle the volume series
     (destroying any volume -> price relationship, keeping the price path),
     re-derive the signals and compare the forward return of the flagged
     bars with the observed one.  1,000 shuffles give an empirical p-value.
  2. Does it survive real costs?  Re-run the engine at 10 bps fee + 5 bps
     slippage (the project defaults) on the best sweep cell.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from bot import db as store
from bot.backtest import run_backtest
from bot.config import BotConfig, CostConfig, RiskConfig
from bot.data.feed import Bar
from bot.strategy.volume_climax import VolumeClimax

SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")
TIMEFRAMES = ("1d", "4h")
HORIZON = 20
SHUFFLES = 1000


def bars_to_df(bars) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


def forward_returns(df: pd.DataFrame, horizon: int) -> np.ndarray:
    """Return from the next bar open to the close `horizon` bars later."""
    entry = df["open"].shift(-1)
    exit_ = df["close"].shift(-horizon)
    return (exit_ / entry - 1.0).to_numpy(dtype=float)


def climax_mask(df: pd.DataFrame, z: float, drop_pct: float, close_pos: float) -> np.ndarray:
    strat = VolumeClimax(vol_z=z, drop_pct=drop_pct, close_pos=close_pos)
    prep = strat.prepare(df)
    mask = (
        (prep["vol_z"] >= z)
        & (prep["drawdown"] <= -drop_pct)
        & (prep["close_pos"] >= close_pos)
    )
    return mask.fillna(False).to_numpy(dtype=bool)


def main() -> None:
    conn = store.connect()
    frames = {}
    for sym, tf in itertools.product(SYMBOLS, TIMEFRAMES):
        frames[(sym, tf)] = bars_to_df(store.load_bars(conn, sym, tf))
    conn.close()

    z, drop_pct, close_pos = 2.5, 0.05, 0.5
    rng = np.random.default_rng(7)

    print(f"== climax events at z>={z}, drop>={drop_pct:.0%}, close in upper half")
    print(f"   forward {HORIZON} bars from the next open, vs the same horizon on every bar")
    pooled_obs, pooled_all, pooled_perm = [], [], []
    for (sym, tf), df in frames.items():
        fwd = forward_returns(df, HORIZON)
        mask = climax_mask(df, z, drop_pct, close_pos)
        valid = ~np.isnan(fwd)
        observed = float(np.nanmean(fwd[mask & valid]))
        baseline = float(np.nanmean(fwd[valid]))
        n = int((mask & valid).sum())
        print(
            f"   {sym:8s} {tf:2s} n={n:4d}  event {observed * 100:+6.2f}%"
            f"  all-bars {baseline * 100:+6.2f}%  lift {(observed - baseline) * 100:+5.2f}pp"
        )
        pooled_obs.append((observed, n))
        pooled_all.append(baseline)

        perm = []
        for _ in range(SHUFFLES):
            shuffled = df.copy()
            shuffled["volume"] = rng.permutation(df["volume"].to_numpy())
            pm = climax_mask(shuffled, z, drop_pct, close_pos) & valid
            if pm.any():
                perm.append(float(np.nanmean(fwd[pm])) - baseline)
        pooled_perm.append(np.array(perm))

    n_tot = sum(n for _, n in pooled_obs)
    obs_lift = sum((o - b) * n for (o, n), b in zip(pooled_obs, pooled_all)) / n_tot
    perm_lift = np.concatenate(pooled_perm)
    p_value = float((perm_lift >= obs_lift).mean())
    print(f"\n   POOLED lift {obs_lift * 100:+.2f}pp over {n_tot} events")
    print(f"   shuffled-volume lift: mean {perm_lift.mean() * 100:+.2f}pp,"
          f" 95th pct {np.quantile(perm_lift, 0.95) * 100:+.2f}pp")
    print(f"   p-value (shuffled >= observed) = {p_value:.3f}")

    print("\n== cost sensitivity: best sweep cell (z=2.5, drop 8%, rr 3) through the engine")
    params = dict(vol_z=2.5, drop_pct=0.08, close_pos=0.5, rr=3.0, max_hold=20)
    for label, costs in (
        ("zero fees", CostConfig(fee_bps=0.0, slippage_bps=0.0)),
        ("10bps fee + 5bps slip", CostConfig(fee_bps=10.0, slippage_bps=5.0)),
    ):
        print(f"   [{label}]")
        for (sym, tf), df in frames.items():
            bars = [
                Bar(
                    ts=r.ts, open=float(r.open), high=float(r.high), low=float(r.low),
                    close=float(r.close), volume=float(r.volume),
                )
                for r in df.itertuples()
            ]
            cfg = BotConfig(
                initial_cash=1000.0,
                costs=costs,
                risk=RiskConfig(risk_per_trade=0.02, max_position_pct=0.95),
                bars_per_year=365.0 if tf == "1d" else 365.0 * 6,
            )
            res = run_backtest(bars, VolumeClimax(**params), cfg)
            m = res.metrics
            print(
                f"     {sym:8s} {tf:2s} {int(m['trades']):3d} trades"
                f"  win {m['win_rate'] * 100:5.1f}%  ret {m['total_return'] * 100:+6.1f}%"
                f"  pf {m['profit_factor']:4.2f}"
            )


if __name__ == "__main__":
    main()
