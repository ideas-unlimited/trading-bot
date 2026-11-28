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
- the forward runner reproduces the backtest's exits: `bot forward` replays
  the same `Engine`, so the stop, take-profit and time exit are the measured
  ones. `bot live` manages the same stop and fixed-R target locally on each
  closed bar (stop checked first), so it no longer drops the target.

Paper capital is **$1,000 per bot/strategy**. It is simulated money; the point
is that every strategy is measured on the same stake.

Start the paper record with the forward runner (outside `BOT_LAB` it refuses
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

**Changing the runner's `--risk-*` flags rescales the whole curve and does not
move the window, so the deploy mark is not re-taken** and every sleeve shows a
phantom move "since deploy". After such a change, delete the affected
`.../forward/status.json` files: the next cycle re-pins at the current equity.
Done for `capitulation_dip_uni` and `jup_liquidity_15m` on 2026-11-28, where
moving the unit from 5%/50%/10% to the sleeves' 3%/95%/no-brake turned UNI from
a correct +38.5% history into a fake -17.5% "live since deploy" in two minutes.

### Release steps on the box (the step that was missed for UNI)

A promotion only **authorizes** a strategy. Nothing runs until two different
things are live on the box, and confusing them is how UNI sat flat for hours
on 2026-11-28 while its forward sleeve replayed and the Bots page stayed
empty:

- **`tradingbot-forward.service`** (already running) picks the new manifest
  up on its next cycle and replays the sleeve as a simulated wallet. This is
  the measurement harness - no orders, never on the Bots page.
- **`tradingbot@<name>`** is the actual bot. Until it is started, the owner's
  question "where is my bot?" has the honest answer "nowhere".

**The standard: a paper promotion is complete only when BOTH halves are
live** - the forward sleeve replaying and the `tradingbot@<name>` unit
running. A forward-only sleeve is a deliberate exception (e.g. two sleeves
on one symbol) and must say so in its manifest evidence.

Release is one command on the box, after commit -> push -> `sudo bash
/opt/trading-bot/deploy/update.sh`:

    sudo bash /opt/trading-bot/deploy/release_paper.sh <bot-name> <strategy-name>

It reads the manifest (symbol, timeframe, capital and the three measured
risk knobs from `evidence`), writes `/etc/tradingbot/<name>.env`, starts
`tradingbot@<name>`, verifies the demo-account journal line, the Bots-page
registration and the heartbeat, and refuses any manifest whose evidence
does not carry `risk_per_trade`, `max_position_pct` and `max_drawdown_stop`.
It never clobbers an existing env file and is idempotent - re-running a
released bot just verifies it.

After releasing (and any time the question "where is my bot?" comes up):

    sudo bash /opt/trading-bot/deploy/reconcile_paper.sh

Walks every released bot against the checklist below and prints PASS/FAIL.

The manual steps the script performs, for understanding or when it is
unavailable:

1. **Manifest landed.** `journalctl -u tradingbot-forward -n 20` shows the
   new sleeve under `active runner(s)`, and
   `/var/lib/tradingbot/paper/<strategy>/forward/status.json` exists.
2. **Start the bot itself.** Create `/etc/tradingbot/<name>.env`
   (name is a short bot name: `uni`, `jup`, ...):

   ```
   SYMBOL=UNI/USDT
   INTERVAL=4h
   STRATEGY=capitulation_dip_uni
   CAPITAL=1000
   STATE=/var/lib/tradingbot/paper/uni.json
   POLL=60
   BOT_ENV=paper
   RISK_PER_TRADE=0.05
   MAX_POSITION_PCT=0.50
   MAX_DRAWDOWN_STOP=0.10
   ```

   plus `HEARTBEAT_URL=` copied from `/etc/tradingbot/forward.env` (copy it
   on the box; never paste it into chat). `chmod 640 root:trader` the file.
   Then `sudo systemctl enable --now tradingbot@<name>`.

   **The three risk knobs are not optional.** `run_bot.sh` defaults them to
   1% / 95% / a 10% brake, which is nobody's measured profile. They must
   match exactly what the sleeve was backtested at (read the research note
   or the evidence `command`). On 2026-11-28 all five live bots were caught
   running at those defaults hours after release - same signals, wrong
   sizing, and the swing sleeves would have halted on their own historical
   drawdown because their evidence says run the brake off (0.99). The
   forward unit has its own `--risk-per-trade/--max-position-pct/
   --max-drawdown-stop` on its ExecStart; one runner serves every sleeve, so
   a sleeve measured at a different profile is only mirrored exactly by its
   own `bot live` process.
3. **Verify the bot is really alive**, all four:
   - `journalctl -u tradingbot@<name>` shows "paper trading <SYMBOL> @ <tf>
     on Binance DEMO account" and the first poll line (anchored to now,
     skipping historical bars). **A fresh bot is flat until the next bar
     close** - "position=flat" on a 4h bot at 08:30 is correct, not a fault.
   - the monitor really answers:
     `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/api/bots`
     must print `200`, and the body must list the bot. A 500 or an empty
     body is a broken monitor, not a quiet book: on 2026-11-28 that endpoint
     raised `TypeError: Object of type Decimal is not JSON serializable`
     (NUMERIC columns) and the Bots page showed no bots at all while every
     unit and heartbeat was healthy.
   - `/var/lib/tradingbot/paper/<name>.json` holds `last_ts`, and once the
     bot is in a trade it holds **both** `stops` and `tps` for the symbol.
     A stop without a target means the bot will hold past its fixed-R exit:
     `bot live` checked only the stop until 2026-11-28, so a sleeve measured
     with a target ran a different rule live than in its backtest.
   - the Bots page shows it running with the position, stop and target.
4. **Trade alerts are shared, not per-bot.** Every live
   `tradingbot@<name>` reads `/etc/tradingbot/alerts.env`; there is no
   per-bot alert setting. Configure it once (README, "Trade alerts"), then
   confirm this bot's startup log says `[live] trade alerts: ntfy` (or
   `telegram` / `email`). If it says `trade alerts off`, fix the shared
   file and restart the bot. Run
   `bot notify-test --env-file /etc/tradingbot/alerts.env` to check delivery.
   Never add alerts to `tradingbot-forward`: that is the shadow replay.
5. **Box checks:** `/etc/tradingbot/` must be `root:trader 750` - the
   `trader` user has to traverse it to read its own env file (this silently
   broke every bot on 2026-11-28). One bot per symbol (they share one demo
   account, so two bots read the same balance and close each other's
   position). **If the symbol already has a bot, the new sleeve stays
   forward-only - do not start a second unit.** `capitulation_dip_jup` is in
   exactly that state while `jup_liquidity_15m` trades JUP: the 4h sleeve
   accrues its record in the forward replay and the live slot stays with the
   bot that already owns the symbol. `STATE` lives under `/var/lib/tradingbot/`. A sub-4h strategy
   keeps the 1-year evidence cap everywhere, including here.
6. **Reconcile after every release.** Re-walk this checklist against what
   is actually running - `sudo bash /opt/trading-bot/deploy/reconcile_paper.sh`
   does it automatically: units active, env risk knobs equal the measured
   profile, heartbeat fresh, forward sleeve present, no errors in
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
| **every fill within 30 bps of the signal bar open** | execution fidelity, measurable from the first trade |
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
               "note": "forward-only release (reason) goes here if applicable"},
  "active": true,
  "demoted_at": "",
  "demote_reason": ""
}
```

**The evidence must carry the measured risk profile** (`risk_per_trade`,
`max_position_pct`, `max_drawdown_stop`): the release script generates each
bot's env file from it, and `deploy/reconcile_paper.sh` diffs the running
config against it. A promotion without those three fields is unreleasable
by the script - by design, since omitting them is how every live bot ended
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
