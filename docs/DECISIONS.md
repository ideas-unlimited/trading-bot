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
| `trading_paper` | bots, bot_trades, equity_snapshots, events, runs, promotions |
| `trading_prod`  | same schema, real money, its own promotions audit |

Separate databases, not schemas, so the paper role physically cannot write
prod rows. One schema file applied to both env DBs so they cannot drift.

## D4 - Activation is a manifest

`promotions/paper/<name>.json` and `promotions/prod/<name>.json` carry
strategy, params hash, symbols, timeframe, capital, evidence, promoter.
Unlisted strategies are never imported. Startup fails loud rather than
trading flat and silent.

## D5 - Promotion is a package, path-guarded

lab -> paper package: strategy module, its tests, its paper manifest. Nothing
else. Framework edits take the core route (branch from master, full suite
green). `bot promote` writes the manifest; keeping the diff to those three
paths is enforced by review, not by a script.

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

## D13 - Two public hostnames, one per world (2026-11-28)

`paper.139-99-236-166.sslip.io` -> :9000 (`trading_paper`) and
`prod.139-99-236-166.sslip.io` -> :9001 (`trading_prod`) are the only public
dashboards; the old bare hostname 302s to paper so an old bookmark cannot
land on the wrong world.

The env is injected server-side into the same HTML (title, green/red banner,
`body.paper|prod`, accent colour), not by a client-side switch: a cached page
can never claim the wrong world. Prod rejects bot and preset create/delete
with a read-only error - strategies arrive by promotion package, never by
clicking "create" against the live database.

What it commits us to: both worlds run the same master checkout; only
`BOT_ENV` differs per systemd unit. A UI change that hides the banner, or a
new mutating endpoint that skips the read-only guard, is a regression.

## D14 - The dashboards show only promoted strategies (2026-11-28)

The Strategies tab used to list the whole code registry on every env, so
prod showed six strategies that could not run there. That was misleading:
the catalogue must equal what can trade.

`/api/strategies` now returns only the active promotion manifests for the
env. Lab (`BOT_LAB=1`, set by `start_trader.cmd`) is the exception: it lists
the full registry because lab is where ideas are tried. Bot and preset
creation outside lab require an active manifest, and a bot must match the
manifest's symbols and timeframe - the promotion package is the unit of
trust.

What it commits us to: a strategy visible on the paper dashboard has a
manifest behind it, and prod shows nothing until paper promotes a package.

## D15 - One credential file per world; prod refuses demo balances (2026-11-28)

Both web units shared `/opt/trading-bot/.env`, and the venue client only
ever read `BINANCE_DEMO_KEY`. So the prod dashboard displayed the demo
wallet as if it were prod's account. No code path could reach real money
yet (the order path is demo-only), which made the display harmless today
and dangerous as a habit: a production banner on virtual numbers teaches
the operator to trust the wrong screen.

Rails, in order of severity:

1. `BinanceLiveClient` loads `.env.<env>` (per-world) before `.env`, and a
   process environment variable always wins over both. Demo keys now live
   in `/opt/trading-bot/.env.paper`; `.env` holds only shared settings.
2. `BOT_ENV=prod` makes the client raise before it reads any key or
   returns any balance. Prod cannot show a demo wallet, cannot place a
   demo order, and cannot be given one by configuration. The dashboard
   says *live venue not configured* and explains why.
3. `/api/account` labels every payload with `venue` (`demo` or `live`) and
   returns per-asset quantities, USDT price and value, so the screen can
   never show an unlabelled balance.
4. A real-money venue is a deliberate build (keys, endpoint, review), not
   a flag flip. Until it exists, prod trading is impossible by design.

What it commits us to: adding live trading means new code and a new
review, and the demo rail stays in place regardless.

## D16 - Run rate is measured over a window, never extrapolated from two points (2026-11-28)

The dashboard shows each live bot's equity, return since start, heartbeat age
and a run rate. Run rate is defined as P&L per day over the last seven days
of `equity_snapshots`, reported with its window and sample count, plus the
monthly equivalent at that rate.

Rules: fewer than two snapshots or under ~70 minutes of history shows an
em dash, not a number. A row whose heartbeat is older than five minutes
reads *no heartbeat* even if its status still says running - the database
does not get to pretend a dead process is alive. `bot live` records equity
and heartbeat every poll (opt-in via `record=True`, so tests never touch a
database), and bookkeeping failures never stop trading.

## D17 - Trailing is a preference for trend sleeves, not a rule (2026-11-28)

The owner prefers a trailing exit over a fixed profit target, so a winner is not
capped before a trend has a chance to run. That is a preference, not a
condition: it is applied where it has been measured to pay, and an exit is
otherwise chosen per strategy by measurement.

Where it pays: the gated 4h book (+65.3% / -13.8% with a 1.5 ATR Chandelier
trail against +50.0% / -23.4% with the fixed stop, same entries) and
`momentum_swing`, which ships with no hard target and a 3.5 ATR trail.

Where it does not: `wvfx_stoch`. A tight trail (1.5-3.5 ATR, 48 variants)
survived out of sample 2 times; a wide trail with no target (5-7 ATR) raised the
average out-of-sample trade (+3.2% vs +1.6%, positive on 5 of 5 majors) but
lowered the account return (+16.6% median vs +20.5%) by holding ~75% longer.
Mean reversion on a panic spike is the case where a target beats patience. See
`research/WVFX_TUNE.md` and `research/wvfx_trailfit.py`.

What it commits us to: keeps the 2.5R target as the `wvfx_stoch` default, keeps
the trail in `BookConfig.trail_atr` and `momentum_swing`, and forbids applying
either exit style to a new strategy without measuring it on that strategy. A
genuine conflict is resolved by the holdout, not by the preference - but with
the evidence level, the preference is the tie-breaker.