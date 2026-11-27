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

- Secrets live in `.env` (git-ignored). Current keys: `ALPACA_KEY_ID`,
  `ALPACA_SECRET_KEY`, `BINANCE_DEMO_KEY`, `BINANCE_DEMO_SECRET`,
  `DATABASE_URL`. The Binance pair is a *demo trading* key: it authenticates
  against `demo-api.binance.com` only and holds virtual balances.
- **Never** paste secrets in chat, never write them into files via tools
  (the sandbox refuses, rightly). Ask the user to edit `.env` themselves.

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
  with **zero API calls**. History is hard-capped at **3 years**
  (`MAX_HISTORY_DAYS`), auto-pruned after every upsert.
- **Prefer the Postgres bars cache over new CSV pipelines.** If a research
  fetcher must read elsewhere (e.g. Binance), upsert into `bars` so the data
  lands in one place.

## Alpaca API

- Trading (paper only): `https://paper-api.alpaca.markets` — **hard-coded**;
  this project must never reach a live broker.
- Market data: `https://data.alpaca.markets` (NOT `data-api.alpaca.markets`
  — that host no longer resolves; it cost us a debugging session).
- Free plan rate limit: 200 req/min — the delta-sync cache above is the
  mechanism that respects it.

## Live paper books (`bot auto`)

- `start_bots.cmd [seconds]` runs the three research books as $1,000 paper
  wallets (A gated 3%, B gated 5%, C plain 3%) every 15 minutes, detached,
  logging to `logs\bots_<ts>.log`. `python -m bot auto --once` is the same
  cycle for Task Scheduler. **Nothing is ordered** - the wallets are simulated
  so three books can hold the same coin at different sizes.
- Each cycle: `scanlib.load_scan_bars` delta-syncs the cache, the still-forming
  bar is dropped, then `simulate_book(..., close_at_end=False)` replays each
  book. Because the replay is deterministic, a restart cannot corrupt state;
  `runs/autobooks/status.json` holds every wallet, position and signal, and
  `history` vs `live since deploy` are reported separately (deploy equity is
  pinned on the first cycle - that line is the honest forward number).
- `simulate_book` force-closes open positions at the last bar by default (the
  backtest convention); live books pass `close_at_end=False`. Keep that flag
  when touching the engine or the live books will look permanently flat.
- Poll just after a 4h close (00/04/08/12/16/20 UTC). The replay fills at the
  next bar's true open whenever it runs, so a late poll is exact against the
  backtest but optimistic against a real order; prompt polling keeps it honest.

## Market-data venues

- The crypto books were researched on **Binance** klines, so live signals read
  Binance too: `load_scan_bars(sym, tf, source="binance")` (public REST, no
  key, cached in `bars` like everything else). Alpaca's crypto feed is the same
  market from another venue; a 0.04% price difference moved the three-year
  gated book from +581.6% to +184.5% because one cross landed a bar early.
  Do not mix venues inside one book.
- Alpaca stays the source for stocks and for the existing `bot live` loop.
- **Spot only (rule restated 2026-11-27).** Cash spot balances: no futures
  (`/fapi`), no margin (`/sapi` margin endpoints), no leverage, no shorts.
  The Binance broker only ever touches `/api/v3/*`.
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
- Strategies should import from `bot.analysis` rather than re-implementing
  math inline; simple MAs stay in `bot.indicators`.

## Backtest windows (hard rules from the user)

- **Three-year cap.** Nothing older than the last three years goes into a
  backtest or a strategy search (`MAX_HISTORY_DAYS`). The clock is the max
  bar in the data, so the window always ends at the newest bar.
- **Sub-4h means one year.** Any test, sweep or search on a timeframe faster
  than 4h (1h, 30m, 15m, 5m ...) may only use the **last 12 months**. A
  faster chart has more bars per unit of history, so a longer window there
  multiplies noise and multiple-testing, not evidence. Compare a 1h result
  against a 4h result on the same 12 months, never against a 3-year 4h run.
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
- One bot per symbol: they share one Alpaca paper account, so two bots on
  the same symbol would close each other's position.
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
  venue-matching bot or its data sync there; a paper book fed by Alpaca does
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
- **Public dashboard:** `https://139-99-236-166.sslip.io` — Caddy terminates
  TLS (Let's Encrypt via the sslip.io name, auto-renewing) and enforces basic
  auth, proxy to `127.0.0.1:9000`. Only 22/80/443 are open; port 9000 is not
  reachable from the internet. The app has **no auth of its own** and its
  endpoints create/delete bots, so Caddy is the only thing protecting it —
  never bind the app to 0.0.0.0 or open 9000. Set up with
  `deploy/public_web.sh`; the password hash lives in `/etc/caddy/Caddyfile`,
  the plaintext is not stored anywhere on the box. Access log:
  `/var/log/caddy/dashboard.log`.
