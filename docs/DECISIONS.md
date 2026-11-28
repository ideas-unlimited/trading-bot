# Decisions (append-only)

What was agreed, why, and what it commits us to. Peers: read this before
touching environments, branches or promotions; `AGENTS.md` has the short form.

## D1 - One repo, two branches (2026-11-27)

`master` is the deployable line: reviewed core plus strategies that earned a
paper slot. `lab` is where ideas live and die; it is never deployed. Core
fixes land on master once and propagate to paper and prod on the next pull.

Rejected: separate repos per environment (code drift, paper stops predicting
prod), and prod tracking an unpinned branch (a lab commit would reach money).

## D2 - paper and prod are deployments, not branches

Both boxes run master. What differs is which strategies are activated: the
loader imports only modules named in `promotions/<env>/*.json`, so a module
can be present on a box and inert there. Never fork code per environment.

## D3 - Three databases

| DB | Holds |
| --- | --- |
| `trading_common` | `bars` only (market data, environment-agnostic) |
| `trading_paper` | bots, bot_trades, equity_snapshots, events, runs, strategies |
| `trading_prod`  | same schema + `promotions` |

Separate databases, not schemas, so the paper role physically cannot write
prod rows. One schema file applied to both env DBs so they cannot drift.

## D4 - Activation is a manifest

`promotions/paper/<name>.json` and `promotions/prod/<name>.json` carry
strategy, params hash, symbols, timeframe, capital, evidence, promoter.
Unlisted strategies are never imported. Startup fails loud rather than
trading flat and silent.

## D5 - Promotion is a package, path-guarded

lab -> paper package: strategy module, its tests, its paper manifest. Nothing
else. Framework edits take the `--core` route (branch from master, full suite
green). `deploy/promote.sh` refuses any other path in the diff.

## D6 - Paper gate: 2 weeks, $1,000, execution checks

- 2 weeks live on paper, at least one completed fill (plumbing proof).
- No round-trip count gate.
- $1,000 paper capital per bot/strategy.
- Zero duplicate orders, zero unexplained rejects.
- Fills within 30 bps of the signal bar open.
- Fees + slippage within 10% of the 10 bps model.
- Unbroken heartbeat, no manual intervention.
- Paper outperforming its own backtest blocks promotion (lookahead smell).
- Divergence and drawdown are reported, enforced only at 30+ round trips.

## D7 - Gates are defaults: override and demote are first-class

`promote --force --reason` records the override, the failed checks and the
operator, and badges the bot while it is the reason it is live.
`demote` drains by default (no new entries, stop still managed, exits on its
own signal) or `--flatten` closes at market. History is append-only.
Hard invariants are never overridable: spot only, long only, demo keys cannot
point at production, kill switch.

## D8 - Logging

Runtime logs are production artifacts, not workspace scratch:
`/var/log/tradingbot/{paper,prod}/<bot>.log`, JSON lines, rotated, plus
journald per unit. The `events` table carries signals, orders, rejects, fills,
stops and errors into the UI. Never log keys or signatures - only
`clientOrderId` and the venue error code.

## D9 - Two dashboards, unmistakable

Paper on :9000, prod on :9001. Persistent banner and title (PAPER - simulated
fills / PRODUCTION - REAL MONEY), env on every card. Prod UI is read-only
except the kill switch: no create/delete endpoints there.

## D10 - Bot lifecycle is data

`bots` gains status, started_at, stopped_at, last_heartbeat_at, capital,
strategy_name, params_hash, git_tag. New tables: `bot_trades` (fees, realized
PnL, closed_by), `equity_snapshots` (bot_id, ts, equity, cash, position),
`events`. Without these there is no start time, no PnL curve and no way to
debug a fill.

## D11 - Prod deploys are tagged

Every prod deployment is tagged `prod-vN` and records the commit sha, so
"what is trading money right now" is answerable from `git describe`. A daily
drift check alerts when prod is behind master on live/risk/exchange paths.

## D12 - One venue: Binance spot (2026-11-28)

Alpaca is gone. Signals, backtests, scans, the live loop and the dashboard all
read Binance spot klines from `api.binance.com` (public, no key, cached in
`trading_common.bars`); orders exist only on `demo-api.binance.com` with demo
keys. There is no stock, futures, margin or short path, and no second venue to
reconcile.

Why: two feeds for one market produced contradictory fills and confused the
team. The decisive example is still in `AGENTS.md` - a 0.04% price difference
moved the gated book from +581.6% to +184.5% because one cross landed a bar
early.

What it commits us to: any new instrument must be a Binance spot pair; a new
venue is a new decision, not a config flag. `BinanceLiveClient` is the only
adapter the loop, CLI and web may use, and every order carries a deterministic
`clientOrderId`.
