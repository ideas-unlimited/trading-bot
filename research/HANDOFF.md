# HANDOFF — conviction swing strategy research (pause 2026-11-26)

Resume point for the "analyze top-5 crypto, build conviction-gated swing
strategies (4h/1d), $1k, 0 fees" task. Read this + AGENTS.md first.

## User requirements (fixed)
- Top 5 coins by mcap, no stables: BTC, ETH, BNB, SOL, XRP (Binance USDT pairs).
- Swing trades, 4h and 1d bars only.
- User wants "only take trades over 70% conviction/chance of success".
- Capital $1,000, 0 fees/slippage assumption (user's simplification).
- **HARD LIMIT: max 2 years of data** for backtesting/strategy generation
  (~2024-11-26 .. 2026-11-26). All research must respect this. Findings below
  were measured on the old three-year window; they are historical, not a
  current 2-year result.

## Data
- `data/crypto/*_{4h,1d}.csv` refreshed via `research/fetch_binance.py`
  (Binance, not Alpaca — no rate-limit impact). Bars through 2026-11-26.
- AGENTS.md prefers the Postgres `bars` cache for app data; CSVs are the
  research source (pre-existing pipeline).

## Key findings (validated, honest)
1. **Per-trade P(win) >= 0.70 does not exist in this data.** Three model
   families agree: peer's logistic (walk_forward, purged), peer's bucket
   empirical model, my GBM (`research/conviction_model.py`, sklearn HGB).
   - 1d AUC 0.485 (worse than chance; high-p buckets win LESS than base).
   - 4h AUC 0.520 (mild; top bucket 42.6% vs 36.6% base — real but tiny).
   - Base rates (3y): P(target 1.5R before stop 2ATR) ~ 0.36-0.40;
     P(exit pnl>0, time exit) ~ 0.41-0.47; direction-right ~ 0.50.
   - Calibrated probabilities miscalibrated (predict 0.98 for 0.39 reality).
2. **High win rate != profit.** Tight targets (rr 0.5, stop 1.5-2.0 ATR)
   realize 65-68% wins but EV ~ 0 (1d: -6.6% total, 4h: -10.9%). The money
   is in ~44-52% win rates with 1.5-2.0R targets.
3. **Structural edge = bull-regime breakout, asymmetric management.**
   Gate: coin close > ema50 AND btc close > sma200 (regime) AND
   close within 2% of rolling 20-bar high (dist_hi20 > -0.02). Entry next
   bar open; stop = mult*ATR14; target = rr*stop distance; time exit.
4. **Walk-forward sensitivity (3y, risk 2%/trade, max_pos 2, 0 fees):**
   - 4h robust region: stop 2.0-3.0 x rr 1.5-2.0 x h 30-50 all positive
     (+112% .. +255%). Best cell stop=2.5 rr=2.0 h=40: +255.5%, CAGR 52.6%,
     Sharpe 1.78, dd -22.3%, n=293, win 48.8%, avgR +0.302, pf 1.63.
   - 1d best: stop=2.0 rr=2.0 h=30: +56.2%, Sharpe 1.10, dd -24.2%, n=69,
     win 46.4%, avgR +0.351, pf 1.69. Neighbors positive.
5. **TRUE holdout check (IS 2023-11..2025-11 -> OOS 2025-11..2026-11):**
   IS-best 4h config (2.5/2.0/40, IS Sharpe 2.53) made **-1.4% OOS**
   (Sharpe 0.05, dd -13.5%, win 40%, avgR +0.081, pf 1.15). All top-6
   configs OOS: -15.7% .. +8.7%, avgR +0.05..+0.09 — edge real but modest,
   much weaker in the 2026 regime. Do NOT oversell the +255% number.
6. **Per-coin (4h 2.5/2.0/40, 3y):** BNB EV +0.47 (n=90), ETH +0.46 (n=52),
   BTC +0.16 (n=106), SOL +0.15 (n=33), XRP +0.07 (n=12 — rarely qualifies).

## Open question at pause point
**Adaptive conviction gate** (interrupted mid-run): gate = regime + breakout
AND trailing per-coin stats of the setup class (past-only, shift(1)):
lookback 480/720/1080 bars, thresholds trail_win >= 0.45/0.50 and
trail_ev >= 0.0/0.05. Purpose: skip the class when its recent edge is gone
(would have avoided 2026 chop). Script was about to run — recreate from
HANDOFF text or `git show` if lost. Baseline to beat: 2.5/2.0/40 no gate
(+255.5% 3y, but -1.4% in final year).

## Peer coordination
- Peer agent (daf33c7e) works the same request; owns research/model.py,
  sim.py (portfolio sim engine I reuse), run*.py, year_*.py, fastsim.py.
  They were validating year-by-year robustness; fetch_1h.py/year_1h.py
  exist (1h is OUTSIDE user's requested 4h/1d — flag if they push it).
- Peer b34a1a19 added AGENTS.md (read it; pip quirks, Postgres conventions,
  3y cap, shared-file warnings).
- Division agreed (message sent): peer = model/sim validation; me = final
  strategy class + tests + report. Their sim.simulate gate input is a
  `p` column (p>=threshold) — deterministic rules are expressed as p=1/0.

## My files (created this session)
- `research/conviction_model.py` — sklearn HGB walk-forward conviction model
  (3y cutoff, labels y_pos = exit pnl>0). Keep for the report's evidence.
- `research/validate_geom.py` — geometry gate builder + walk-forward runner
  (exports gate_frame; setup=brk/dip/both, regime=bull/any).
- `research/sensitivity.py` — 36-config grid + monthly win rate.
- `runs/conviction_oos_{1d,4h}.csv` — full-data OOS predictions (pre-3y-cap;
  rerun if needed for the report).
- sklearn installed in .venv (scikit-learn 1.9.1) — pyproject.toml not yet
  updated (AGENTS.md asks to; add a `research` optional extra).

## Remaining work (tomorrow)
1. Finish adaptive conviction gate test (see above); pick final config.
2. Implement `ConvictionSwing` strategy class in `src/bot/strategy/`
   (re-read `src/bot/strategy/__init__.py` before editing — shared file).
   Needs: regime gate with graceful fallback if no btc column; ATR stop;
   R-multiple target; time exit (engine supports CLOSE via strategy state).
3. Tests (follow tests/test_strategy_donchian.py pattern).
4. Update pyproject.toml (scikit-learn research extra) + run test suite.
5. Final HTML report (equity curves, tables, honest 70% reframe):
   - per-trade 70% win probability is unattainable; deliver conviction as
     regime+setup+trailing-EV gate; expected profile ~44-52% wins with
     avgR +0.1..+0.3, ~50% positive months.
   - Capital allocation for $1k: 2% risk/trade (~$20 initial risk),
     max 2 concurrent positions, 3-bar cooldown, 0 fees caveat vs reality.
6. Consider appending env lessons to AGENTS.md (stdout buffering with -u,
   900s timeouts, sklearn install).

## Reporting honesty rules
- Never present the 3y +255% as the expected return; holdout year was -1.4%.
- 0 fees is the user's assumption; real-world costs will reduce edge.
- No lookahead anywhere (signals fill next bar open; trailing stats shift(1)).
