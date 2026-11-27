"""Reproduce the WVF+Stoch $1,000 shared-wallet result."""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
sys.path.insert(0, "research")

import wvfx_reanalyze as wr  # noqa: E402
import wvfx_portfolio as wp  # noqa: E402


def main() -> None:
    feats = wr.all_feats("4h")
    for _sym, _df in feats.items():
        _c = _df["close"]
        _df["coin_bear"] = (
            _c < _c.rolling(1200, min_periods=1200).mean()
        ).to_numpy()

    def rule(_s, _i, row, _btc_bull, _bear):
        return (
            bool(row["green_event"])
            and bool(row["over_5"])
            and bool(row["cross_up"])
            and bool(row["coin_bear"])
        )

    for fee in (0.0, 15.0):
        eq, tr, _ = wp.simulate(
            feats, rule, stop_mult=2.0, rr=2.0, max_hold=40,
            exit_cross=False, risk_frac=0.015, max_pos=3,
            max_pos_pct=0.34, fee_bps=fee, bear_only=True,
        )
        st = wp.stats(eq, tr)
        print(
            f"fee={fee:4.1f}bps  n={st['n']:3d}  "
            f"ret={st['ret']*100:+.2f}%  dd={st['dd']*100:+.2f}%  "
            f"sharpe={st['sharpe']:.2f}  win={st['win']*100:.1f}%  "
            f"avgR={st['avgR']:+.3f}  pf={st['pf']:.2f}"
        )


if __name__ == "__main__":
    main()