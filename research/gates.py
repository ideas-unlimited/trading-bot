"""Regime gates from the "10 highest-conviction" list, tested on our winners.

Idea 1 (regime-gated execution) and idea 5 (BTC/ETH lead-lag correlation) are
the two that fit a long-only spot book. This wraps the best per-symbol cells in
a gate that only ever blocks entries, never changes the exit:

  none        - the sleeve as tuned
  btc_trend   - no new entry while BTC is below its own 100-day average
  lockstep    - no new entry while the coin's 30-day correlation to BTC > 0.85
  chop        - no new entry while the coin's 30-day efficiency ratio < 0.30

Same one-year window, same engine, same costs; the gate shares the engine so
fills stay next-open and the exits are untouched.

    python research/gates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from allcoins_sweep import BAR_HOURS, LOAD_DAYS, WINDOW_DAYS, grids, run_one  # noqa: E402
from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from bot.strategy.base import Strategy  # noqa: E402

SLEEVES = [
    ("NEAR/USDT", "lab pullback 120h s2 2R"),
    ("JUP/USDT", "capdip 240h d20 rr1.5"),
    ("RENDER/USDT", "lab breakout 120h s2 t3"),
    ("ARB/USDT", "lab breakout 120h s2 t3"),
    ("ATOM/USDT", "lab pullback 120h s2 2R"),
    ("TAO/USDT", "momentum_swing 120/480h t5.0"),
    ("SUI/USDT", "momentum_swing 240/960h t3.5"),
    ("ONDO/USDT", "vol_climax z2 d6 hold48h"),
]
GATES = ("none", "btc_trend", "lockstep", "chop")


class Gated(Strategy):
    PARAMS = ()

    def __init__(self, inner, btc: pd.DataFrame | None = None, mode: str = "none",
                 n: int = 180) -> None:
        self.inner = inner
        self.btc = btc
        self.mode = mode
        self.n = n

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.inner.prepare(df)
        self.warmup = self.inner.warmup
        if self.mode == "none" or self.btc is None:
            return df
        b = self.btc.set_index("ts")["close"].reindex(df["ts"]).ffill()
        df["_btc"] = b.to_numpy()
        df["_btc_ma"] = b.rolling(600).mean().to_numpy()
        coin_ret = df["close"].pct_change(self.n)
        btc_ret = pd.Series(df["_btc"]).pct_change(self.n)
        df["_corr"] = coin_ret.rolling(self.n).corr(btc_ret).to_numpy()
        move = df["close"].diff().abs().rolling(self.n).sum()
        df["_er"] = ((df["close"] - df["close"].shift(self.n)).abs() / move).to_numpy()
        self.warmup = max(self.warmup, self.n + 5, 610)
        return df

    def signal(self, i: int, df: pd.DataFrame, position: float):
        if position == 0 and i >= self.warmup and self.mode != "none":
            row = df.iloc[i]
            if self.mode == "btc_trend":
                if not (row["_btc"] > row["_btc_ma"]):
                    return None
            elif self.mode == "lockstep":
                if row["_corr"] > 0.85:
                    return None
            elif self.mode == "chop":
                if row["_er"] < 0.30:
                    return None
        return self.inner.signal(i, df, position)


def main() -> None:
    btc = bars_to_frame(load_scan_bars("BTC/USDT", "4h", source="binance", days=LOAD_DAYS))
    rows = []
    for sym, label in SLEEVES:
        df = bars_to_frame(load_scan_bars(sym, "4h", source="binance", days=LOAD_DAYS))
        start_ts = df["ts"].iloc[-1] - pd.Timedelta(days=WINDOW_DAYS)
        base = dict(grids("4h"))[label]
        for mode in GATES:
            strat = Gated(base, btc=btc, mode=mode)
            m = run_one(strat, df, 365.0 * 24.0 / BAR_HOURS["4h"], start_ts)
            rows.append({"symbol": sym, "sleeve": label, "gate": mode,
                         "ret": m["ret"], "dd": m["dd"], "trades": m["trades"],
                         "wins": m["wins"], "is_ret": m["is_ret"],
                         "oos_ret": m["oos_ret"], "score": m["score"]})
            print(f"  {sym:>11} {mode:<10} {m['ret']:+7.1%} trades {m['trades']:>3}", flush=True)
        base.reset() if hasattr(base, "reset") else None
    d = pd.DataFrame(rows)
    d.to_csv(HERE / "gates.csv", index=False)
    piv = d.pivot_table(index=["symbol", "sleeve"], columns="gate", values="ret")
    print("\n=== one-year return by gate ===")
    print(piv.to_string(float_format=lambda v: f"{v:+.1%}"))
    piv2 = d.pivot_table(index=["symbol", "sleeve"], columns="gate", values="dd")
    print("\n=== drawdown by gate ===")
    print(piv2.to_string(float_format=lambda v: f"{v:+.1%}"))
    print("\n=== gates that beat the ungated sleeve ===")
    for gate in GATES[1:]:
        sub = d[d.gate == gate]
        base = d[d.gate == "none"].set_index(["symbol", "sleeve"]).ret
        beat = sum(1 for _, r in sub.iterrows() if r.ret > base.loc[(r.symbol, r.sleeve)])
        print(f"  {gate:<10} better on {beat}/{len(sub)} sleeves")


if __name__ == "__main__":
    main()