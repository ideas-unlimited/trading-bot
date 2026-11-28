# Promotion: lab -> paper -> prod

One repo, one code line. What changes between environments is **which
strategies are activated**, never which code runs. A core fix merged to
`master` reaches paper and prod on the next pull; a strategy reaches them only
when a manifest names it.

    lab      experiments, dead ends. Never deployed.
      |
      |  promote a package: strategy module + tests + promotions/paper/<name>.json
      v
    master   reviewed core + paper-grade strategies. Deployed to both boxes.
      |
      +--> paper box: activates promotions/paper/*.json   (demo keys, $1,000 per bot)
      +--> prod box:  activates promotions/prod/*.json    (live keys)

The loader imports nothing that a manifest does not name, so a strategy module
can sit on a box and be completely inert there.

## Names used here

- **Dry-run bot** (also *dry-run sleeve*): the replay process. It reads bars,
  writes `.../forward/status.json`, and never sends an order.
- **Trading bot**: the demo-order loop started as
  `tradingbot@<strategy>-<env>`. It is the only half that can fill, alert or
  appear on the Bots page.

The historical words survive in commands, units, paths and API keys:
`bot forward`, `tradingbot-forward.service`, `/etc/tradingbot/forward.env`,
`/api/forward`, and `.../forward/status.json`. Those are identifiers, not the
user-facing names. Read `forward` as dry-run and `live` as trading below.

## 1. lab -> paper

Package contents, and nothing else:

    src/bot/strategy/<name>.py
    tests/test_<name>.py
    promotions/paper/<name>.json

`bot promote` writes the manifest, computes its `params_hash` and prints one
PASS/FAIL line per gate check; commit it with the module and the test. There
is no `promote.sh`: the path rule is enforced by review, so keep the package
diff to those three files. Framework edits take the core route instead - branch
from master, full suite green, reviewed merge.

Entry requirements:

- full test suite green locally;
- the manifest carries the backtest evidence: window (2-year cap, sub-4h =
  1 year), strategy params hash, symbols, timeframe, and the run's metrics -
  the gate reads `bars`, `window_days`, `trades`, `fee_bps`,
  `slippage_bps` and `params_hash` (the hash of `--params`);
- the dry-run runner reproduces the backtest's exits: `bot forward` replays
  the same `Engine`, so the stop, take-profit, trailing stop and time exit
  are the measured ones. `bot live` manages the same stop, fixed-R target
  and Chandelier trail locally on each closed bar (stop checked first, then
  the trail is raised for the next bar), so it no longer drops either one.

Paper capital is **$1,000 per bot/strategy**. It is simulated money; the point
is that every strategy is measured on the same stake.

Start the paper record with the dry-run runner (`bot forward`; outside `BOT_LAB` it refuses
to start without the active manifest and refuses a parameter hash that does
not match it):

    python -m bot forward --all --poll 900

State is one folder per sleeve: `runs/<env>/<strategy>/forward/status.json`
plus `<SYMBOL>.trades.csv` and `<SYMBOL>.equity.csv`. `--all` serves every
active manifest from one process and re-reads the manifests each cycle, so a
promote/demote takes effect without editing a unit; `--strategy ... --symbol
...` runs one sleeve by hand. A cycle is deterministic and idempotent; the
deploy mark is pinned on the first cycle and re-pinned (old date kept as
`repinned_at`) only when the two-year window start moves. On the server one
systemd unit runs it: `tradingbot-forward.service`, state under
`/var/lib/tradingbot/paper/<strategy>/forward/`.

**Each sleeve replays the risk profile recorded in its own manifest.**
`bot forward --all` reads `risk_per_trade`, `max_position_pct` and
`max_drawdown_stop` from the manifest evidence; the unit's `--risk-*` flags
are only the fallback for an older manifest that omits them (or when
`--profile flags` deliberately asks for a what-if). Most paper sleeves are
3% / 95% / no brake, but `capitulation_dip_uni` and `jup_liquidity_15m` are
5% / 50% / a 10% brake. A profile change is a different book, so the deploy
mark is re-taken and `repinned_at` keeps the old date; the status payload
records the profile and its source. Flattening all sleeves onto one global
profile made UNI read +38.5% in history instead of its measured +67.8%.

### Release steps on the box (the step that was missed for UNI)

A promotion only **authorizes** a strategy. Nothing runs until two different
things are live on the box, and confusing them is how UNI sat flat for hours
on 2026-11-28 while its dry-run sleeve replayed and the Bots page stayed
empty:

- **`tradingbot-forward.service`** (the dry-run runner, already running) picks the new manifest
  up on its next cycle and replays the sleeve as a simulated wallet. This is
  the measurement harness - no orders, never on the Bots page.
- **`tradingbot@<strategy>-paper`** is the actual bot. Until it is started,
  the owner's question "where is my bot?" has the honest answer "nowhere".

**Naming convention: every bot is `<strategy>-<env>`.**
`/etc/tradingbot/capitulation_dip_uni-paper.env`, systemd instance
`tradingbot@capitulation_dip_uni-paper`, state
`/var/lib/tradingbot/paper/capitulation_dip_uni-paper.json`. Never a bare
symbol or a bare strategy name: one symbol may carry several strategies,
and this one box hosts both paper and prod, so only `<strategy>-<env>` is
unambiguous (the same strategy may run in both worlds at different capital).

**The standard: a paper promotion is complete only when BOTH halves are
live** - the dry-run sleeve replaying and the `tradingbot@<strategy>-paper`
unit running. A dry-run-only sleeve is a deliberate exception (e.g. two
sleeves on one symbol) and must say so in its manifest evidence.

### Why the two records do not match tick for tick

They are two different instruments, not two copies of one account. A release
does **not** clone the dry-run book into the trading bot:

1. **Start state.** The dry-run replays the whole window from a pinned $1,000
   wallet and can already be holding a position that opened before the deploy
   pin. A service trading bot on a fresh state (`STARTUP_REPLAY=1`, the
   `run_bot.sh` default) replays the fetched closed window through `Engine`
   and opens the model's current side as a **new** position at the newest
   close; it does not copy the historical entry price or credit that P&L. A
   manual `bot live` defaults off and anchors flat. On 2026-11-28 the XRP
   dry-run was long from 25 November while its trading bot, released on 28
   November, was flat; startup replay now joins that side, but the entry and
   its P&L start at the release. Either way the bot cannot book the old fill.
2. **Fill price.** The dry-run `Engine` fills a signal at the **next bar's
   open**. The trading bot sends a market order at the **signal bar's close**;
   it cannot wait for an open that has not happened. The same signal therefore
   books a different price and a different P&L.
3. **Wallet.** The dry-run keeps one independent $1,000 wallet per sleeve and
   compounds it. Trading bots share one demo account; each sizes from
   `min(account equity, CAPITAL)` and stops compounding at `CAPITAL`. A loss
   or win in one sleeve changes the equity every other sleeve sizes from. Do
   not compare the two equity curves dollar-for-dollar.
4. **Catch-up.** The dry-run replays every bar in its window. A trading bot
   skips a backlog larger than `max_catchup` and resumes on the newest bars, so
   downtime can remove signals the replay still took.
5. **The honest comparison.** Compare the return and position **since the same
   bar** (`since deploy` / since release), never the dry-run's two-year
   `history` equity against the trading bot's $1,000. Use the execution gate -
   fills within 30 bps of the signal bar's close, the price the bot decided at -
   to judge the bot; the backtest's next-open fill is a separate, expected
   difference. Do not expect identical trades. If either half is absent or was
   started late, fix that before comparing anything.

Release is one command on the box, after commit -> push -> `sudo bash
/opt/trading-bot/deploy/update.sh`:

    sudo bash /opt/trading-bot/deploy/release_paper.sh <strategy-name>

It derives the bot name as `<strategy>-paper`, reads the manifest (symbol,
timeframe, capital and the three measured risk knobs from `evidence`),
writes `/etc/tradingbot/<strategy>-paper.env`, starts
`tradingbot@<strategy>-paper`, verifies the demo-account journal line, the
Bots-page registration and the heartbeat, and refuses any manifest whose
evidence does not carry `risk_per_trade`, `max_position_pct` and
`max_drawdown_stop`. It never clobbers an existing env file and is
idempotent - re-running a released bot just verifies it.

After releasing (and any time the question "where is my bot?" comes up):

    sudo bash /opt/trading-bot/deploy/reconcile_paper.sh

Walks every released bot against the checklist below and prints PASS/FAIL.

The manual steps the script performs, for understanding or when it is
unavailable:

1. **Manifest landed.** `journalctl -u tradingbot-forward -n 20` shows the
   new sleeve under `active runner(s)`, and
   `/var/lib/tradingbot/paper/<strategy>/forward/status.json` exists.
2. **Start the bot itself.** Create `/etc/tradingbot/<strategy>-paper.env`
   (naming convention: `<strategy>-<env>`, never a short symbol name):

   ```
   SYMBOL=UNI/USDT
   INTERVAL=4h
   STRATEGY=capitulation_dip_uni
   CAPITAL=1000
   STATE=/var/lib/tradingbot/paper/capitulation_dip_uni-paper.json
   POLL=60
   BOT_ENV=paper
   RISK_PER_TRADE=0.05
   MAX_POSITION_PCT=0.50
   MAX_DRAWDOWN_STOP=0.10   # read this sleeve's manifest evidence
   ```

   plus `HEARTBEAT_URL=` copied from `/etc/tradingbot/forward.env` (copy it
   on the box; never paste it into chat). `chmod 640 root:trader` the file.
   Then `sudo systemctl enable --now tradingbot@<strategy>-paper`.

   **The three risk knobs are not optional.** `run_bot.sh` defaults them to
   1% / 95% / a 10% brake, which is nobody's measured profile. They must
   match exactly what the sleeve was backtested at: read the manifest
   evidence, not a global runner flag. On 2026-11-28 the trading bots were
   caught running at those defaults hours after release - same signals, wrong
   sizing, and the swing sleeves would have halted on their own historical
   drawdown because their evidence says run the brake off (0.99). The
   dry-run runner resolves each sleeve's profile from its manifest (the unit's
   `--risk-*` flags are fallback only); a trading bot carries the same three
   knobs in its env. `bot live` enforces the drawdown brake as well as the
   dry-run engine does; the remaining difference is the shared account, not
   the brake.
3. **Verify the bot is really alive**, all four:
   - `journalctl -u tradingbot@<strategy>-paper` shows "paper trading <SYMBOL> @ <tf>
     on Binance DEMO account" and the first poll line (anchored to now,
     skipping historical bars). **A fresh bot is flat until the next bar
     close** - "position=flat" on a 4h bot at 08:30 is correct, not a fault.
     Every poll re-fetches at least **100 days of the bot's own timeframe**
     plus its indicator warmup from the venue (`LIVE_LOOKBACK_DAYS` in
     `src/bot/cli.py`) - a day-based floor, because a fixed bar count
     shrinks with the timeframe (400 bars is ~66 days on 4h but ~4 days on
     15m). Restarts carry no warmup penalty: indicators are recomputed on
     the full fetched window every poll.
   - the monitor really answers:
     `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/api/bots`
     must print `200`, and the body must list the bot. A 500 or an empty
     body is a broken monitor, not a quiet book: on 2026-11-28 that endpoint
     raised `TypeError: Object of type Decimal is not JSON serializable`
     (NUMERIC columns) and the Bots page showed no bots at all while every
     unit and heartbeat was healthy.
   - `/var/lib/tradingbot/paper/<strategy>-paper.json` holds `last_ts`, and once the
     bot is in a trade it holds `stops`, `tps` when the sleeve has a fixed-R
     target, and `trails` when it has a Chandelier exit.  A trail entry
     carries the peak, ATR distance and arming state, so a restart cannot
     reset or lower the stop.  `bot live` checked only the stop until
     2026-11-28, then added the target; a sleeve measured with a target or
     trail therefore ran a different rule live until both landed.
   - the Bots page shows it running with the position, stop and target.
4. **Trade alerts are shared, not per-bot.** Every live
   `tradingbot@<strategy>-paper` reads `/etc/tradingbot/alerts.env`; there is no
   per-bot alert setting. Configure it once (README, "Trade alerts"), then
   confirm this bot's startup log says `[live] trade alerts: ntfy` (or
   `telegram` / `email`). If it says `trade alerts off`, fix the shared
   file and restart the bot. Run
   `bot notify-test --env-file /etc/tradingbot/alerts.env` to check delivery.
   Never add alerts to `tradingbot-forward`: that is the dry-run replay.
5. **Box checks:** `/etc/tradingbot/` must be `root:trader 750` - the
   `trader` user has to traverse it to read its own env file (this silently
   broke every bot on 2026-11-28). One bot per symbol (they share one demo
   account, so two bots read the same balance and close each other's
   position). **If the symbol already has a bot, the new sleeve stays
   dry-run-only - do not start a second unit.** `capitulation_dip_jup` is in
   exactly that state while `jup_liquidity_15m` trades JUP: the 4h sleeve
   accrues its record in the dry-run replay and the trading slot stays with the
   bot that already owns the symbol. `STATE` lives under `/var/lib/tradingbot/`. A sub-4h strategy
   keeps the 1-year evidence cap everywhere, including here.
6. **Reconcile after every release.** Re-walk this checklist against what
   is actually running - `sudo bash /opt/trading-bot/deploy/reconcile_paper.sh`
   does it automatically: units active, env risk knobs equal the measured
   profile, heartbeat fresh, dry-run sleeve present, no errors in
   `journalctl`. Every incident so far was a release step done halfway, not
   a code bug.
7. **Secrets:** any credential touched during release goes into
   `C:\Git\tradingbot-secrets-2026-11-28.txt` (the master record) in the same
   edit that puts it anywhere else.

Demotion and halting need none of this: `bot demote` and the Bots-page kill
switch work on running units without restarting anything.

## 2. paper -> prod

Add `promotions/prod/<name>.json`. **No code changes** -- the code has been in
master since the lab -> paper promotion.

Eligibility:

| Requirement | Why |
|---|---|
| **2 weeks live** on paper | long enough for the scheduler, the data sync and a real order to happen at least once |
| **no duplicate orders, no unexplained rejects** | the failure that actually costs money |
| **every fill within 30 bps of the signal bar's close** | execution fidelity against the price the bot decided at; the backtest fills next open |
| **fees + slippage within 10% of the 10 bps model** | cost model is not fiction |
| **unbroken heartbeat, no manual intervention** | the process was alive the whole time |
| **paper must not beat its own backtest by a wide margin** | the best lookahead detector we have |

Deliberately **not** gated on round-trip count. Quality metrics (divergence,
drawdown) are **reported** on the dashboard and are only *enforced* once a
strategy has 30+ round trips; below that they are noise, not evidence.

Prod starts at **25% of paper capital** ($250) for the first four weeks, then
the promoted size.

## 3. Manifest fields

```json
{
  "strategy": "capitulation_dip_uni",
  "env": "paper",
  "params_hash": "sha256:1f3c...",
  "symbols": ["UNI/USDT"],
  "timeframe": "4h",
  "capital": 1000,
  "promoted_at": "2026-11-28T13:00:00+00:00",
  "promoted_by": "sahil",
  "override": false,
  "override_reason": "",
  "checks": [{"name": "backtest_bars", "ok": true, "detail": "bars=4381"}],
  "evidence": {"bars": 4381, "window_days": 730, "trades": 19,
               "fee_bps": 10, "slippage_bps": 5,
               "params_hash": "sha256:1f3c...",
               "risk_per_trade": 0.05, "max_position_pct": 0.5,
               "max_drawdown_stop": 0.1,
               "note": "dry-run-only release (reason) goes here if applicable"},
  "active": true,
  "demoted_at": "",
  "demote_reason": ""
}
```

**The evidence must carry the measured risk profile** (`risk_per_trade`,
`max_position_pct`, `max_drawdown_stop`): the release script generates each
bot's env file from it, and `deploy/reconcile_paper.sh` diffs the running
config against it. A promotion without those three fields is unreleasable
by the script - by design, since omitting them is how every trading bot ended
up at `run_bot.sh` defaults on 2026-11-28.

## 4. These gates are defaults, not walls

The owner can promote or demote any strategy at any time. The system's job is
to make the decision visible, not to refuse it.

**Override.** 'bot promote --force --reason "..."' promotes even when a check
fails. The manifest records override=true, the reason, the failed checks
verbatim, and who did it. The dashboard shows an OVERRIDE badge next to that
bot for as long as the override is the reason it is live.

**Demote.** 'bot demote <strategy> [--flatten]':

- default (drain): the manifest entry goes inactive immediately, the bot opens
  no new positions, keeps managing the existing stop and exits on its own
  signal or stop;
- '--flatten': market-close the position now, then go idle.

Either way the bot stays running and reports 'revoked' rather than vanishing,
so the dashboard shows why it is flat.

**Hard invariants override cannot touch.** Spot only, long only, demo keys can
never point at production, and the kill switch. Those are code, not policy; no
manifest can enable futures, margin or shorting.

## 5. Audit

Every promotion, override and demotion is an append-only promotions row: who,
when, which checks passed or failed, the reason string, and the evidence
snapshot at that moment. Removing a strategy never deletes its history - a
demoted strategy still shows its live statistics and its exit reason.
