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
  ones. `bot live` is still stop-only; do not use it to validate a
  target-bearing sleeve.

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

Full release, in order, after commit -> push -> `sudo bash
/opt/trading-bot/deploy/update.sh`:

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
   ```

   plus `HEARTBEAT_URL=` copied from `/etc/tradingbot/forward.env` (copy it
   on the box; never paste it into chat). `chmod 640 root:trader` the file.
   Then `sudo systemctl enable --now tradingbot@<name>`.
3. **Verify the bot is really alive**, all three:
   - `journalctl -u tradingbot@<name>` shows "paper trading <SYMBOL> @ <tf>
     on Binance DEMO account" and the first poll line (anchored to now,
     skipping historical bars);
   - `curl -s http://127.0.0.1:9000/api/bots` lists the bot with a fresh
     `last_heartbeat_at`;
   - the Bots page shows it running.
4. **Box checks:** `/etc/tradingbot/` must be `root:trader 750` - the
   `trader` user has to traverse it to read its own env file (this silently
   broke every bot on 2026-11-28). One bot per symbol (they share one demo
   account). `STATE` lives under `/var/lib/tradingbot/`. A sub-4h strategy
   keeps the 1-year evidence cap everywhere, including here.
5. **Secrets:** any credential touched during release goes into
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
               "params_hash": "sha256:1f3c..."},
  "active": true,
  "demoted_at": "",
  "demote_reason": ""
}
```

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
