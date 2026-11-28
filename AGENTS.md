# AGENTS.md — shared operational notes for agents & humans

Read this before working in `C:\Git\trading-bot`. It captures machine-specific
lessons so that "learning from one is learning for all". Keep it current:
when you hit a new environment quirk and solve it, add it here and commit.

## Python & packages (Windows quirks)

- **Always use the project venv**: `.venv\Scripts\python.exe` (Python 3.13).
  Global/system pip installs are blocked by the sandbox and pollute the user
  machine — do not attempt them.
- **pip hangs on this machine** if it touches `C:\Users\Home\AppData`
  (blocked folder + keyring deadlock). Two mitigations already applied:
  - `.venv\pip.ini` sets `no-cache-dir` (created via
    `pip config set --site global.no-cache-dir true`)
  - install with: `pip install --no-cache-dir --keyring-provider disabled <pkg>`
- If the venv has no pip: `.venv\Scripts\python.exe -m ensurepip`
- After any dependency change, update `pyproject.toml` too and run the tests.
- Run tests: `PYTHONPATH=src .venv\Scripts\python.exe -m unittest discover -s tests`

## Secrets

- Credentials are **per world**: `.env.<env>` is loaded before `.env`, and
  the process environment beats both. On the server the demo pair lives in
  `/opt/trading-bot/.env.demo`; `.env` holds only shared settings
  (`DATABASE_URL`, heartbeat). The Binance pair is a *demo trading* key: it
  authenticates against `demo-api.binance.com` only and holds virtual
  balances.
- **Live refuses demo credentials outright** (`BinanceOrderClient` raises
  before reading a key). There is no live venue wired, so live cannot show a
  balance or place an order; the dashboard says *live venue not configured*.
  Adding real-money routing is a deliberate code change plus review, never a
  config flip.
- **The master record is `C:\Git\tradingbot-secrets-2026-11-28.txt`** —
  outside the repo, never committed. Every new credential, hostname, token or
  password goes there in the same edit that adds it anywhere else; that file
  is the thing to read when a value is lost, not the chat log.
- A Binance demo secret is shown **once** at creation. If it is lost, delete
  that demo key and create a new pair — never guess or reuse.
- **Never** paste secrets in chat, and never commit them. If a secret does
  land in a transcript, treat it as exposed and rotate it.

## Postgres

- Local server on `localhost:5432` (PostgreSQL 19, admin user `postgres`).
- **Three databases** (agreed 2026-11-27), app user `tradingbot`, credentials
  in `DATABASE_URL` (the base URL; only the database name is swapped):
  - `trading_common` — `bars`, `book_snapshots` (market data, both envs)
  - `trading_demo` — bots, bot_trades, equity_snapshots, events, runs, promotions
  - `trading_live` — same schema, real money, plus its own promotions audit
  `BOT_ENV=demo|live` picks the world; `db.connect_common()` reaches the data
  DB. The app user cannot create databases: bootstrap once with
  `psql -U postgres -f deploy/create_databases.sql`. Legacy `trading_bot`
  still holds the pre-split bars; they were copied into `trading_common`.
- Schema auto-creates on first connect (`CREATE TABLE IF NOT EXISTS` and
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) — nothing destructive.
- Market-data policy: bars are **the** data store. Incremental sync lives in
  `src/bot/scanlib.load_scan_bars` — first run deep-backfills, later runs
  fetch only the delta since the last cached bar, fresh caches are served
  with **zero API calls**. History is hard-capped at **2 years**
  (`MAX_HISTORY_DAYS`), auto-pruned after every upsert.
- **Prefer the Postgres bars cache over new CSV pipelines.** If a research
  fetcher must read elsewhere (e.g. Binance), upsert into `bars` so the data
  lands in one place.
- **Every NUMERIC column comes back as a `Decimal`, and `json.dumps` refuses
  it.** Never hand a database row straight to `json.dumps`: the web layer
  ran one through and `/api/bots` died with `TypeError: Object of type
  Decimal is not JSON serializable` - the connection closed with no body, so
  the Bots page showed nothing while all five bots were healthy (fixed
  2026-11-28). Pass payloads through `bot.web._jsonify`, which also turns
  NaN into null; `_send_json` keeps `_json_default` as the backstop.

## Dry-run and trading demo books (`bot auto`)

- **Names.** A **dry-run bot** is the `dryrun` replay
  (`bot dryrun`, `tradingbot-dryrun.service`, `/api/dryrun`): no orders, no
  Bots row. A **trading bot** is the `deploy` loop
  (`tradingbot@<name>.service`): demo orders, fills, alerts, Bots row. Commands,
  units, paths and API keys use the new spellings; `live` means real money.
- `start_bots.cmd [seconds]` runs the five research books as $1,000 demo
  wallets (A gated 3%, B gated 5%, C plain 3%, D room-gated 3%, E gated
  entries + 1.5 ATR trail) every 15 minutes, detached,
  logging to `logs\bots_<ts>.log`. `python -m bot auto --once` is the same
  cycle for Task Scheduler. **Nothing is ordered** - the wallets are simulated
  so the books can hold the same coin at different sizes.
- Each cycle: `scanlib.load_scan_bars` delta-syncs the cache, the still-forming
  bar is dropped, then `simulate_book(..., close_at_end=False)` replays each
  book. Because the replay is deterministic, a restart cannot corrupt state;
  `runs/<env>/autobooks/status.json` holds every wallet, position and signal, and
  `history` vs since-deploy are reported separately (deploy equity is pinned
  on the first cycle - that line is the honest dry-run number).
- **Trailing stop (added 2026-11-28).** `BookConfig.trail_atr` is a Chandelier
  trail that many ATRs under the highest high since entry, ratcheted up only,
  applied after the intrabar stop check so no bar can both raise the trail and
  fill against it. On the gated 4h book it is the best single change found:
  +65.3% and -13.8% drawdown against +50.0%/-23.4% for the fixed stop (two
  years, five coins, 3% risk), a plateau over 1.25-2.0 ATR, identical at 2-5
  slots, and strictly worse when `trail_after_r` delays arming. It only works
  behind the filter - trailing the raw cross loses money.
- **Timeframe and universe beat each other up.** 6h and 1d were both
  measured: neither beats 4h for this system (1d gives 8 trades in two years;
  6h is worse on returns and drawdown). Widening the coin pool from 5 majors
  to 20 collapses the trail book to +8% with a -49% drawdown because the
  score ranking buys the strongest recent mover. Do not widen the universe
  without re-measuring that ranking.
- **Per-symbol tuning is the policy; naive fitting is the failure mode.**
  `docs/TARGETS.md` is authoritative: one strategy per symbol, "tune on the
  earlier slice, validate on the later slice". This bullet explains why the
  second half of that rule exists, and is not a licence to pool parameters.
  Measured 2026-11-28: fit each coin separately on year 1 (fast/slow x trail
  grid, Calmar-ranked, >=8 trades, <=-30% dd floor) and the fitted config lost
  to a shared baseline on **5 of 5 coins** in year 2; with a single per-symbol
  knob (trail only) it won **2 of 5** - a coin flip - picks scattered
  (2.5, 1.0, 2.5, fixed, 1.5). Cause: at 4h one coin supplies 9-16 trades a
  year, so a three-knob fit has under four decisions per parameter. The fix
  is more decisions per symbol (several sleeves/patterns per coin, or a
  longer-holding variant), not a bigger grid on one symbol. Never select the
  per-symbol parameter set on the slice that reports the result: tune on the
  earlier part, report the later part, and publish the symbol's own year1 /
  year2 table. See `research/per_symbol.py`, `research/per_symbol_knob.py`
  and `research/PER_SYMBOL.md` (peer analysis, same conclusion).
- **The dry-run sleeve runner is the same replay for one strategy.**
  `bot dryrun --all` walks every active manifest in one loop and runs each
  strategy/symbol through `Engine` (stop, target, trailing stop and time
  exit included), with one folder per sleeve:
  `runs/<env>/<strategy>/dryrun/status.json` plus `<SYMBOL>.trades.csv` and
  `<SYMBOL>.equity.csv`. One process serves all sleeves and re-reads the
  manifests each cycle, so promote/demote needs no unit edit; a broken
  sleeve is logged and skipped, not fatal. `--strategy ... --symbol ...`
  replays one sleeve by hand and refuses outside `BOT_LAB` without an
  active manifest, and refuses a parameter set whose hash differs from the
  promoted one. Use it to validate a target-bearing sleeve. `bot deploy` now
  manages the stop, the fixed-R target and the Chandelier trail locally
  (Binance spot has no brackets), checked against each closed bar with the
  stop first, so a bar that touches both is booked as a loss exactly like
  the engine. The trail is persisted as `trails` next to `stops`/`tps`: the
  peak and ATR distance survive a restart, the stop only ratchets upwards,
  and the trail is applied after the bar's exit check so no bar can both
  raise the stop and fill against it. A trading bot therefore runs the same
  exit rule its backtest measured; before 2026-11-28 it silently ignored
  `take_profit` and the trail, exiting only on the stop or a signal.
- **A dry-run and a trading bot are not tick-for-tick twins.** The dry-run
  replays from history and can already hold a position opened before its
  deploy pin. A service trading bot (`STARTUP_REPLAY=1`, the `run_bot.sh`
  default) reconstructs the strategy's current stance on a fresh state and
  opens that side as a **new** position at the newest closed bar's close; it
  never copies the historical entry price or credits that P&L. A manual
  `bot deploy` keeps the conservative anchor-and-stay-flat default. The dry-run
  fills at the next bar's open, while the trading bot fills at the signal
  bar's close. The dry-run has one independent $1,000 wallet per sleeve that
  compounds; all trading bots share one demo account, size from
  `min(account equity, CAPITAL)` and stop compounding at `CAPITAL`. A backlog
  beyond `max_catchup` is skipped by the deploy loop but replayed dry-run.
  Compare since the same bar, never history equity against the trading wallet.
  The 2026-11-28 XRP mismatch (dry-run long from 25 November, bot released
  flat) is what startup replay addresses: the bot can now join the same side,
  but its entry price, entry time and P&L start at the release.

- **Each sleeve replays the risk profile its manifest was measured at.**
  `bot dryrun --all` reads `risk_per_trade`, `max_position_pct` and
  `max_drawdown_stop` from each manifest's evidence; the unit's `--risk-*`
  flags are only the fallback for older manifests that omit them (or when
  `--profile flags` deliberately asks for a what-if). Most demo sleeves are
  3% / 95% / no brake (`0.99`), but `capitulation_dip_uni` and
  `jup_liquidity_15m` are 5% / 50% / a 10% brake. A profile change is a
  different book: the deploy pin is re-taken and `repinned_at` keeps the old
  date. Per-bot env files must carry the same three knobs
  (`RISK_PER_TRADE`, `MAX_POSITION_PCT`, `MAX_DRAWDOWN_STOP`); one global
  runner profile produced phantom moves (UNI read +38.5% instead of its
  measured +67.8%). Left at 5%/50%/10%, ARB, RENDER and NEAR halted on their
  own 10-13% historical drawdown and their history stopped matching evidence
  (NEAR read -9.6% instead of +86%). `bot deploy` applies the drawdown brake to
  new entries and persists the peak in `state["risk"]`, so a restart cannot
  reset the protection.
- **A balance below the venue's lot step is dust, not a position.** Binance
  accepts an order quantity only as a whole multiple of the pair's `stepSize`
  (JUPUSDT: step 0.1, `minQty` 0.1, `minNotional` 5 USDT), while entries are
  sized in *quote* terms (`quoteOrderQty`), so a position is routinely
  fractional. Exiting sells `floor(balance / step) * step` and strands the
  remainder: closing 1,004.0949 JUP left 0.0949 that no order can ever sell
  (below 0.1 x $0.24, about two cents). The loop used to read that crumb as an
  open position and retry the sale every poll (`quantity 0.0949 floors to 0.0`).
  `_position_qty()` now floors against the client's own rules, calls a sub-step
  leftover flat and logs it once (`dust_logged` in the state file); the dust
  stays on the venue, and chasing it is not worth a code path.

- **Trading fills alert; the dry-run replay is silent.** Every trading
  `tradingbot@<name>` bot reads `/etc/tradingbot/alerts.env` (template
  `deploy/alerts.env.example`) and sends one email/Telegram/ntfy message per
  entry and exit, subject `strategy:side:price: entry/sl/tp`. The dry-run is
  a shadow replay, so its unit does not read that file; `bot dryrun --alerts`
  opts in manually, and its fill-key cursor then prevents history replay.
  Verify with `bot notify-test --env-file ...`; alerts off by default, and an
  alert failure is logged, never fatal.
- **The pin is per window.** If the replay window's start moves by more than
  two days (history cap changed, a coin delisted, backfill), the pin is
  re-taken at the current equity and `repinned_at` records the old deploy
  date. Without that, a 3y-to-2y cap change showed every wallet at -78%
  "since deploy" while its history was +50% - a phantom loss from comparing
  two different books.
- **The trading loop writes to the database.** `bot deploy` registers itself once
  (name `<SYMBOL>-<strategy>`) and records equity plus a heartbeat every poll,
  so the dashboard can answer "what is running and at what rate" from
  `bots`/`equity_snapshots` rather than from the log. Failures are logged
  once and never stop trading. Run rate = P&L/day over the last 7 days, with
  its window shown; under ~70 minutes it is blank, not extrapolated.
- `simulate_book` force-closes open positions at the last bar by default (the
  backtest convention); demo books pass `close_at_end=False`. Keep that flag
  when touching the engine or the demo books will look permanently flat.
- Poll just after a 4h close (00/04/08/12/16/20 UTC). The replay fills at the
  next bar's true open whenever it runs, so a late poll is exact against the
  backtest but optimistic against a real order; prompt polling keeps it honest.

## Market-data venues

- **One venue.** Signals, backtests, scans, the trading loop and the dashboard
  all read Binance spot: `load_scan_bars(sym, tf, source="binance")` (public
  REST, no key, cached in `bars` like everything else). There is no second
  feed to reconcile — a 0.04% price difference from another venue once moved
  the gated book from +581.6% to +184.5% because one cross landed a bar early.
- Orders exist only on `demo-api.binance.com` via `BinanceOrderClient`; no
  stock, futures, margin or short path exists, and none should be added.
- **Spot only (rule restated 2026-11-27).** Cash spot balances: no futures
  (`/fapi`), no margin (`/sapi` margin endpoints), no leverage, no shorts.
  The Binance broker only ever touches `/api/v3/*`.
- **USDT is the quote currency** (agreed 2026-11-28): every bot pair is
  X/USDT and the wallet is kept in USDT, not split across stables. Stray USDC
  is converted by a spot `USDCUSDT` market sell, not held as a second dollar.
- **Binance demo trading (2026-11-27).** Rehearse live orders against
  `https://demo-api.binance.com` — same endpoints, signing and order types as
  production, keys issued from the real account, virtual balances. Verified
  on closed 4h bars: demo opens/closes match production to the cent, highs
  and lows differ by a few dollars and volume by ~0.6%; testnet's book is far
  thinner (volume ~1/70th). So **signals always come from production klines**
  (`api.binance.com`); only order routing points at demo. Never take signals
  from demo or testnet candles.

## Web terminal

- Start/restart: `start_trader.cmd [port]` (default **9000**; 80xx ports are
  used by another project on this machine). It kills the existing listener
  first, relaunches detached, opens the browser, and logs everything to
  `logs\web_<timestamp>.log` (keeps the 10 newest).
- Stop: `stop_trader.cmd [port]`.
- When launching from a script (not the user's PowerShell), use
  PowerShell `Start-Process -FilePath ... -WindowStyle Hidden` so the
  process survives the parent session.

## Bots page (monitor + kill switch)

- **Dry-run sleeves are not bots.** They have no `bots` row, heartbeat or
  kill switch; one process replays every active manifest and writes
  `<state>/<strategy>/dryrun/status.json`. The Dashboard's **Dry-run bots**
  card reads `/api/dryrun` and is the place to see them. The Bots page stays
  empty unless a real `bot deploy` loop is registered.
- Bots are **created and promoted from lab**; demo and live do not have a
  create form. The Bots page is a monitor: per loop it shows state, equity,
  run rate, the position (entry, mark, unrealized P&L), the **local stop and
  target**, any resting venue orders (normally none — entries and exits are
  market orders) and the fills it booked.
- Each fill is written to `bot_trades` (fee from `fee_bps`, realized P&L on the
  sell); the loop also persists its view — position, entry, stop, target, last
  bar — to `bots.state` every poll, so the page needs no venue call to answer
  "in a trade? where is the stop?".
- **"Started" is this run, not the bot's first ever run.** The loop restamps
  `bots.started_at` every time it picks up an existing row (systemd restarts a
  crashed loop onto the same row), and `/api/running` reports `uptime_s` only
  when the heartbeat is fresh — a dead row never reads "up 3d". The Bots card
  and the dashboard's Bots table both show the start time with its uptime.
- **The watchdog is what makes a dead loop visible.** `bot watchdog` runs
  from a 5-minute timer, not from the loop: it reads `bots.last_heartbeat_at`,
  alerts by name (the shared ping URL cannot), and pings the external check
  only while everything is fresh. A halted bot keeps heartbeating, so it is
  fresh, not dead — halt is a choice, silence is a failure.
- **The kill switch** is `bots.halted`, written by `POST /api/bots/<id>/halt`.
  The loop reads it every poll, closes any open position at market and skips
  new entries while halted; it keeps heartbeating, so a killed bot reads
  `halted`, not `dead`. Halting is deliberately allowed in live: stopping
  trading must never wait for a promotion.
- **Retiring a bot is a step, not just a demotion.** `bot demote` makes the
  dry-run runner drop the sleeve on its next pass, but the trading bot's env
  file in `/etc/tradingbot` survives, so `reconcile_demo.sh` keeps expecting
  an active manifest for a bot that is gone — retiring the 15m JUP ladder
  produced three failures on its own. `deploy/retire_demo.sh <strategy>` is
  the counterpart of release: it refuses while the bot still holds a position
  (drain it, or `bot demote --flatten` first), then disables the unit and
  archives the env file under `/etc/tradingbot/retired/`, which reconcile does
  not scan. The dry-run sleeve needs no step at all.

## Analysis toolkit (bot.analysis)

- `src/bot/analysis.py` is the shared "beyond simple tools" layer —
  liquidity/structure (swing pivots, liquidity levels, sweep & reclaim,
  level touches), regime (ADX, efficiency ratio, variance-ratio Hurst,
  vol regime), statistics (z-score, skew/kurt, autocorr, drawdown),
  volume (rolling VWAP, volume z-score, OBV slope, directional volume),
  microstructure (wick ratio, range expansion) and divergences.
- **When the user pastes an indicator** (Pine Script or other), port its
  math into `analysis.py` following the module docstring conventions:
  OHLCV DataFrame in, Series out, NaN on warmup, keyword-only params with
  documented units, and **causal only** — proven by the prefix-stability
  test in `tests/test_analysis.py` (recompute on truncated history, earlier
  values must not change).  Add one provenance comment (source + date)
  and at least one value-sanity test.
- Chart-structure primitives came from a pasted TradingView pattern detector
  (2026-11-27): `trendline_channel` (rails through the last two confirmed
  pivots), `channel_breakout` (close beyond a rail by k ATR, optional
  confirmation bars) and `measured_move` (rail +/- channel height). The twenty
  named patterns in the source are just combinations of those three, so only
  the primitives were ported. `BookConfig.structure_gate` exposes them as
  entry gates: `breakout`, `squeeze` (height/ATR in its own low tail) and
  `room` (measured move must pay `min_rr` x the ATR stop). All three only
  block entries. Breakout and squeeze lost money. `room` is deployed only as
  book D, and only through `trendline_channel_ensemble` (median of spans
  10/15/20, `channel_spans`): the single-span version was a knife edge that
  scored +88% at span 15 between +30% (12) and +15% (18) - tuning that, not
  the strategy. Medianning the spans removed the parameter and the phantom
  edge both; see `research/structure_gates.py` and `structure_tune.py`.
- Strategies should import from `bot.analysis` rather than re-implementing
  math inline; simple MAs stay in `bot.indicators`.

## Backtest windows (hard rules from the user)

- **Two-year cap.** Nothing older than the last two years goes into a
  backtest or a strategy search (`MAX_HISTORY_DAYS`). The clock is the max
  bar in the data, so the window always ends at the newest bar. A coin with
  less history uses everything since its listing; do not fabricate the gap.
- **Sub-4h means one year.** Any test, sweep or search on a timeframe faster
  than 4h (1h, 30m, 15m, 5m ...) may only use the **last 12 months**. A
  faster chart has more bars per unit of history, so a longer window there
  multiplies noise and multiple-testing, not evidence. Compare a 1h result
  against a 4h result on the same 12 months, never against a 2-year 4h run.
- **Long only.** Spot book, no shorts, no margin - a "religion condition"
  the user restated on 2026-11-26. Bear years are survived by sitting in
  cash, not by selling short.
- Report the window with every number. A return without its span and
  timeframe is not a result.

## Repo conventions

- `pine/` — TradingView strategy ports. `research/` — the lab;
  `research/README.md` is the map. Per-symbol sleeves live in
  `research/<symbol>/`; pooled studies sit at the top level while active and
  move to `research/archive/` when closed (one line in its README explains
  what was learned). One study = one script + one `.md` carrying the window,
  timeframe and numbers. Check `git status` and mtimes, and never move or edit
  a file another agent is working in.
- **Per-symbol sleeves are named `<strategy>_<symbol>`** (lowercase symbol,
  e.g. `capitulation_dip_uni`). The deployable class/module lives in
  `src/bot/strategy/`; the symbol's research notes and scripts live in
  `research/<symbol>/`. The suffix is mandatory because a parameter set fitted
  on one coin must never be mistaken for a pooled rule or reused on another.
  **The sleeve is the package**: a static module with its tuned defaults
  baked in, a docstring with the window/numbers that produced them, a registry
  entry and its own test. Do not add a per-symbol JSON parameter shelf - a
  sleeve ships as code, and git history is its audit trail. The plumbing exists
  for research and replay only: `bot.symbolcfg` reads/writes pool configs,
  `BookSpec.symbol_params` carries a coin's overrides through the replay, and
  `bot auto --pool config/target_symbols.json` runs one $1,000 wallet per coin
  (all "book defaults" until a coin ships its sleeve).
- **Stage your own hunks, not your own files.** `web.py`,
  `web/index.html`, `strategy/__init__.py` and this file are edited by several
  agents at once, so one of them can hold someone else's uncommitted work.
  `git add <file>` then sweeps it in (happened 2026-11-28: a peer's finished
  dry-run-card work rode along in a favicon commit). Read `git diff <file>`
  first, stage with `git add -p`, and never `git add -A` or `git commit -a`.
- `src/bot/strategy/__init__.py` is a **shared file** (strategy registry) —
  always re-read it immediately before editing; another agent may have added
  a strategy while you worked.
- Commits: user says "commit push" — commit all coherent work (including
  coordinated peer changes that pass tests) and push to
  `ideas-unlimited/trading-bot` on GitHub.
- Honesty rules of this codebase: no lookahead (signals fill next bar open),
  costs on both sides by default, demo trading only, no live-broker path.
  Do not weaken these.

## Environments & promotion (agreed 2026-11-27)

- **`master` = deployable line** (reviewed core + demo-grade strategies);
  **`lab` = experiments, never deployed.** Core fixes land on master once and
  reach the box on the next pull.
- **demo and live are deployments of master**, separated by activation
  manifests (`promotions/demo/*.json`, `promotions/live/*.json`), env files,
  state dirs and databases — never by branches or forked code.
- Gates, override/demote semantics: `deploy/PROMOTION.md`. Rationale:
  `docs/DECISIONS.md`.
- **Three databases:** `trading_common` (bars only), `trading_demo`,
  `trading_live`. The demo role must not be able to write live rows.
- A strategy runs only if an active manifest names it; the loader must fail
  loud, never trade flat and silent.
- **The venue is part of the world.** Demo is demonstrably virtual; live
  refuses demo credentials before reading a key. Each `/api/account` payload
  carries `venue` and every asset with its USDT value, so no screen can show
  an unlabelled balance.
- **The dashboards list only promoted strategies.** `/api/strategies`
  returns the active manifests for that env; code that merely exists is
  invisible in demo/live. `BOT_LAB=1` (set by `start_trader.cmd`) restores
  the full registry on the laptop, where the whole point is trying ideas.
  Bot and preset creation require an active manifest outside lab, and a bot
  must use a promoted symbol/timeframe. To make a strategy visible, promote
  it (`bot promote ...`), commit `promotions/<env>/<name>.json`, deploy.
- **Team workspaces:** one per person is fine. Sync by `git pull` on master,
  read this file and `docs/DECISIONS.md`, experiment on `lab`, promote as a
  path-guarded package. Do not edit another agent's `research/` notes.

## Server rules (the OVH box is production)

- **Production from day one.** Demo trading on the box is production: that
  loop, that schedule and that state file are the real thing, so a careless
  change there is a production incident even though no money moves.
- **All research and all testing happen locally.** No probes, sweeps,
  backtests, test-suite runs or ad-hoc data pulls on the server.
- **Git only, no file transfers.** No `scp`, `rsync`, or editing files under
  `/opt/trading-bot`. Ship by committing and pushing locally, then
  `sudo bash /opt/trading-bot/deploy/update.sh` on the box.
- **Ask first** for anything beyond routine operation (status, logs, restart,
  pull): the owner grants permission before the command runs.

## Deployment (VPS)

- `deploy/` holds the server bundle: `install.sh` (Debian 13 / Ubuntu 24.04),
  `run_bot.sh`, `backup.sh`, and the systemd units. See `deploy/README.md`.
- Production layout: code in `/opt/trading-bot`, per-bot env files in
  `/etc/tradingbot/<name>.env`, state in `/var/lib/tradingbot/*.json`,
  dumps in `/var/backups/tradingbot`. Services run as the `trader` user,
  the web binds 127.0.0.1:9000/9001 only (reach it over SSH or Tailscale).
- One bot per symbol: every bot shares one demo account, so two bots on the
  same symbol would close each other's position.
- **Heartbeat is two layers, and only the second names the bot.**
  `tradingbot-watchdog.timer` runs `bot watchdog` every 5 minutes: it reads
  `bots.last_heartbeat_at` (written every poll by every loop), alerts through
  `/etc/tradingbot/alerts.env` with subject `BOT STALE: <bot>` when a bot
  has been silent past 10 minutes (repeat 6h, then `BOT RECOVERED:`), and
  pings the dead-man URL in `/etc/tradingbot/watchdog.env` **only when no
  running bot is stale**. Episodes are deduped in
  `/var/lib/tradingbot/<env>/watchdog.json`.
  **Two checks, never one.** `dryrun.env` owns the dry-run *process* check
  (one process, all sleeves, pings unconditionally after every pass);
  `watchdog.env` owns the *book* check (only green when no bot is stale).
  Pointing both at one check makes the gate meaningless, because the dry-run
  ping re-greens it while a trading bot is dead. And never point five
  loops at one ping URL: it stays green while any survivor pings, so one dead
  loop is invisible — exactly the bug the watchdog exists to remove.
  The per-poll ping (`HEARTBEAT_URL` in a bot env file, throttled 30 min)
  still works and is only for a bot you want paged on its own URL. A failed
  ping must never stop the loop. `tradingbot-dryrun.service` runs
  `bot dryrun --all` with state in `/var/lib/tradingbot/demo/`.
- **Trading host (bought 2026-11-27): OVH VPS, Sydney** — 4 vCPU, 8 GB RAM,
  75 GB local disk, 6 months prepaid, automated backup (1 rotation), Linux.
  This is the only box that can reach Binance: `api.binance.com` answers US
  IPs with HTTP 451, so exchange calls must originate outside the US.
- **Demo/live host = the OVH Sydney box, one for both worlds** (cleaned up
  2026-11-28). An earlier plan split demo/UI onto a separate "CometVPS,
  Seattle" box; that was dropped — both dashboards and the dry-run runner run
  on the OVH box, which is also the only one that reaches Binance. Never run
  the venue-matching bot or its data sync from a US-located box; a demo book
  fed elsewhere does not validate a Binance execution venue.
- Measured requirement is ~90 MB per bot/web process and a 28 MB database, so
  the OVH box runs the whole book with >10x headroom and stays under ~3 GB of
  disk. Do not add a database server, cache or queue to it.
- **Provisioned 2026-11-27 (OVH Sydney, 139.99.236.166):** Debian 13, Python
  3.13, PostgreSQL 17. Log in as `debian` with the key
  `sahilnow@gmail.com`; **root login is disabled**, the account has
  passwordless sudo, so every privileged step is `sudo ...`. The service user
  `trader` has no shell — reach the dashboard with
  `ssh -L 9000:127.0.0.1:9000 debian@139.99.236.166`.
  Verified on the box: web returns 200, the test suite passes (457 tests
  locally as of 2026-11-29),
  `load_scan_bars("BTC/USDT","4h",source="binance")` fetches 2,190 bars (no
  451), so the venue path works from this IP.
- **Server layout (agreed 2026-11-27).** `/opt/trading-bot` is a git
  clone — never hand-edit it; commit locally, push, then
  `sudo bash /opt/trading-bot/deploy/update.sh`. Under `~`: `git/` for
  extra clones, `tmp/` for scratch (delete when done), `wip/` is the user's
  (do not touch), `workspace/` is shared project scratch. System config
  stays in `/etc` (`caddy/Caddyfile`, `tradingbot/*.env`) since root daemons
  read it and it belongs to the box backup; the repo holds the template
  (`deploy/Caddyfile.template`) and the renderer that writes it.
- **Public dashboards (two hostnames, one per world):**
  `https://demo.139-99-236-166.sslip.io` -> 127.0.0.1:9000 (`trading_demo`),
  `https://live.139-99-236-166.sslip.io` -> 127.0.0.1:9001 (`trading_live`);
  the bare hostname 302s to demo. Caddy terminates TLS (Let's Encrypt,
  auto-renewing) and basic auth; only 22/80/443 are open. Each page is
  branded at render time (green DEMO / red LIVE banner, distinct
  title, `body.demo|live`), and live rejects bot/preset create+delete:
  strategies arrive by promotion package. The app has **no auth of its
  own**, so Caddy is the only thing protecting it — never bind it to
  0.0.0.0 or open 9000/9001. Render with `deploy/public_web.sh` (reuses the
  existing login and hash); the hash lives in `/etc/caddy/Caddyfile`, never
  the plaintext. Logs: `/var/log/caddy/{demo,live}.log`.
