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
  `/opt/trading-bot/.env.paper`; `.env` holds only shared settings
  (`DATABASE_URL`, heartbeat). The Binance pair is a *demo trading* key: it
  authenticates against `demo-api.binance.com` only and holds virtual
  balances.
- **Prod refuses demo credentials outright** (`BinanceLiveClient` raises
  before reading a key). There is no live venue wired, so prod cannot show a
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
  - `trading_paper` — bots, bot_trades, equity_snapshots, events, runs, promotions
  - `trading_prod` — same schema, real money, plus its own promotions audit
  `BOT_ENV=paper|prod` picks the world; `db.connect_common()` reaches the data
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

## Live paper books (`bot auto`)

- `start_bots.cmd [seconds]` runs the four research books as $1,000 paper
  wallets (A gated 3%, B gated 5%, C plain 3%, D room-gated 3%) every 15
  minutes, detached,
  logging to `logs\bots_<ts>.log`. `python -m bot auto --once` is the same
  cycle for Task Scheduler. **Nothing is ordered** - the wallets are simulated
  so the books can hold the same coin at different sizes.
- Each cycle: `scanlib.load_scan_bars` delta-syncs the cache, the still-forming
  bar is dropped, then `simulate_book(..., close_at_end=False)` replays each
  book. Because the replay is deterministic, a restart cannot corrupt state;
  `runs/<env>/autobooks/status.json` holds every wallet, position and signal, and
  `history` vs `live since deploy` are reported separately (deploy equity is
  pinned on the first cycle - that line is the honest forward number).
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
- **Per-symbol tuning does not survive a holdout (measured 2026-11-28).**
  Fit each coin separately on year 1 (fast/slow x trail grid, Calmar-ranked,
  >=8 trades and <=-30% dd floor) and score on year 2: the fitted config lost
  to the pooled 30/50 + trail 1.5 on **5 of 5 coins**. Narrowing to a single
  per-symbol knob (trail only) still only wins **2 of 5** - a coin flip - and
  the picked values scatter (2.5, 1.0, 2.5, fixed, 1.5) with no regime logic.
  Cause: at 4h over two years one coin supplies 9-16 trades a year, so a
  three-knob fit has fewer than four decisions per parameter. Report every
  symbol's own year1/year2 table, but keep the *parameters* pooled until a
  symbol's trade count can support them - the honest way to earn that is more
  entries per coin (more patterns gated together), not more knobs.
  See `research/per_symbol.py` and `research/per_symbol_knob.py`.
- **The pin is per window.** If the replay window's start moves by more than
  two days (history cap changed, a coin delisted, backfill), the pin is
  re-taken at the current equity and `repinned_at` records the old deploy
  date. Without that, a 3y-to-2y cap change showed every wallet at -78%
  "since deploy" while its history was +50% - a phantom loss from comparing
  two different books.
- `simulate_book` force-closes open positions at the last bar by default (the
  backtest convention); live books pass `close_at_end=False`. Keep that flag
  when touching the engine or the live books will look permanently flat.
- Poll just after a 4h close (00/04/08/12/16/20 UTC). The replay fills at the
  next bar's true open whenever it runs, so a late poll is exact against the
  backtest but optimistic against a real order; prompt polling keeps it honest.

## Market-data venues

- **One venue.** Signals, backtests, scans, the live loop and the dashboard
  all read Binance spot: `load_scan_bars(sym, tf, source="binance")` (public
  REST, no key, cached in `bars` like everything else). There is no second
  feed to reconcile — a 0.04% price difference from another venue once moved
  the gated book from +581.6% to +184.5% because one cross landed a bar early.
- Orders exist only on `demo-api.binance.com` via `BinanceLiveClient`; no
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

- `pine/` — TradingView strategy ports. `research/` — research scratch space
  (currently one agent's active workspace; check `git status`/overlap notes
  before editing there).
- `src/bot/strategy/__init__.py` is a **shared file** (strategy registry) —
  always re-read it immediately before editing; another agent may have added
  a strategy while you worked.
- Commits: user says "commit push" — commit all coherent work (including
  coordinated peer changes that pass tests) and push to
  `ideas-unlimited/trading-bot` on GitHub.
- Honesty rules of this codebase: no lookahead (signals fill next bar open),
  costs on both sides by default, paper trading only, no live-broker path.
  Do not weaken these.

## Environments & promotion (agreed 2026-11-27)

- **`master` = deployable line** (reviewed core + paper-grade strategies);
  **`lab` = experiments, never deployed.** Core fixes land on master once and
  reach both boxes on the next pull.
- **paper and prod are deployments of master**, separated by activation
  manifests (`promotions/paper/*.json`, `promotions/prod/*.json`), env files,
  state dirs and databases — never by branches or forked code.
- Gates, override/demote semantics: `deploy/PROMOTION.md`. Rationale:
  `docs/DECISIONS.md`.
- **Three databases:** `trading_common` (bars only), `trading_paper`,
  `trading_prod`. The paper role must not be able to write prod rows.
- A strategy runs only if an active manifest names it; the loader must fail
  loud, never trade flat and silent.
- **The venue is part of the world.** Paper is demonstrably virtual; prod
  refuses demo credentials before reading a key. Each `/api/account` payload
  carries `venue` and every asset with its USDT value, so no screen can show
  an unlabelled balance.
- **The dashboards list only promoted strategies.** `/api/strategies`
  returns the active manifests for that env; code that merely exists is
  invisible in paper/prod. `BOT_LAB=1` (set by `start_trader.cmd`) restores
  the full registry on the laptop, where the whole point is trying ideas.
  Bot and preset creation require an active manifest outside lab, and a bot
  must use a promoted symbol/timeframe. To make a strategy visible, promote
  it (`bot promote ...`), commit `promotions/<env>/<name>.json`, deploy.
- **Team workspaces:** one per person is fine. Sync by `git pull` on master,
  read this file and `docs/DECISIONS.md`, experiment on `lab`, promote as a
  path-guarded package. Do not edit another agent's `research/` notes.

## Server rules (the OVH box is production)

- **Production from day one.** Paper trading on the box is production: that
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
  `run_bot.sh`, `backup.sh`, and four systemd units. See `deploy/README.md`.
- Production layout: code in `/opt/trading-bot`, per-bot env files in
  `/etc/tradingbot/<name>.env`, state in `/var/lib/tradingbot/*.json`,
  dumps in `/var/backups/tradingbot`. Services run as the `trader` user,
  web binds 127.0.0.1:9000 only (reach it over SSH or Tailscale).
- One bot per symbol: every bot shares one demo account, so two bots on the
  same symbol would close each other's position.
- Heartbeat: every poll pings `HEARTBEAT_URL` (bot env file /
  `--heartbeat-url`, throttled to 30 min) so an outside service alerts when
  the loop stops. It fires on flat cycles too — the point is proving the
  process turns, not that it trades. A failed ping must never stop the loop.
- **Trading host (bought 2026-11-27): OVH VPS, Sydney** — 4 vCPU, 8 GB RAM,
  75 GB local disk, 6 months prepaid, automated backup (1 rotation), Linux.
  This is the only box that can reach Binance: `api.binance.com` answers US
  IPs with HTTP 451, so exchange calls must originate outside the US.
- **Paper/UI host: CometVPS "Standard 8GB", Seattle** — 4 vCPU, 8 GB,
  100 GB NVMe, ~$70/yr. Binance is unreachable from it, so never run the
  venue-matching bot or its data sync there; a paper book fed elsewhere does
  not validate a Binance execution venue.
- Measured requirement is ~90 MB per bot/web process and a 28 MB database, so
  either box runs the whole book with >10x headroom and stays under ~3 GB of
  disk. Do not add a database server, cache or queue to either.
- **Provisioned 2026-11-27 (OVH Sydney, 139.99.236.166):** Debian 13, Python
  3.13, PostgreSQL 17. Log in as `debian` with the key
  `sahilnow@gmail.com`; **root login is disabled**, the account has
  passwordless sudo, so every privileged step is `sudo ...`. The service user
  `trader` has no shell — reach the dashboard with
  `ssh -L 9000:127.0.0.1:9000 debian@139.99.236.166`.
  Verified on the box: web returns 200, 197 tests pass,
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
  `https://paper.139-99-236-166.sslip.io` -> 127.0.0.1:9000 (`trading_paper`),
  `https://prod.139-99-236-166.sslip.io` -> 127.0.0.1:9001 (`trading_prod`);
  the bare hostname 302s to paper. Caddy terminates TLS (Let's Encrypt,
  auto-renewing) and basic auth; only 22/80/443 are open. Each page is
  branded at render time (green PAPER / red PRODUCTION banner, distinct
  title, `body.paper|prod`), and prod rejects bot/preset create+delete:
  strategies arrive by promotion package. The app has **no auth of its
  own**, so Caddy is the only thing protecting it — never bind it to
  0.0.0.0 or open 9000/9001. Render with `deploy/public_web.sh` (reuses the
  existing login and hash); the hash lives in `/etc/caddy/Caddyfile`, never
  the plaintext. Logs: `/var/log/caddy/{paper,prod}.log`.
