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
  `ALPACA_SECRET_KEY`, `DATABASE_URL`.
- **Never** paste secrets in chat, never write them into files via tools
  (the sandbox refuses, rightly). Ask the user to edit `.env` themselves.

## Postgres

- Local server on `localhost:5432` (PostgreSQL 19, admin user `postgres`).
- App database: `trading_bot`, app user: `tradingbot` (credentials in
  `DATABASE_URL`). Schema auto-creates on first connect (`CREATE TABLE IF
  NOT EXISTS`) — nothing destructive.
- Tables: `bars` (market data cache), `bots`, `strategies` (presets),
  `runs`, `paper_trades`.
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

## Web terminal

- Start/restart: `start_trader.cmd [port]` (default **9000**; 80xx ports are
  used by another project on this machine). It kills the existing listener
  first, relaunches detached, opens the browser, and logs everything to
  `logs\web_<timestamp>.log` (keeps the 10 newest).
- Stop: `stop_trader.cmd [port]`.
- When launching from a script (not the user's PowerShell), use
  PowerShell `Start-Process -FilePath ... -WindowStyle Hidden` so the
  process survives the parent session.

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
