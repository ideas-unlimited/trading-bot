# Deploy (Debian 13 / Ubuntu 24.04)

Promotion flow (lab -> paper -> prod): see `PROMOTION.md`.

**The trading box is production from day one — paper trading counts as
production.** It is not a research machine:

- No experiments, probes, sweeps or test runs on the server. Research and
  testing happen on the laptop, where a mistake costs nothing.
- No `scp`, `rsync` or editor sessions against the deployed tree. Code
  reaches the box exactly one way: commit → push → `sudo bash
  /opt/trading-bot/deploy/update.sh`.
- Anything past routine operation (status, logs, restart, pull) needs the
  owner's permission first.

No live broker path exists: orders only ever go to the Binance demo account.

Target hosts:

- **Trading (Binance), paper and prod:** OVH VPS, **Sydney** — 4 vCPU, 8 GB
  RAM, 75 GB local disk, automated backup with 1 rotation. Binance returns
  HTTP 451 to US IPs, so the exchange-connected bots can only run here. Both
  dashboards and the forward runner live on this one box (an earlier plan
  split paper onto a separate "CometVPS Seattle" host; that was dropped
  2026-11-28).

The box needs ~1 GB of RAM and ~3 GB of disk for the whole book (measured
~90 MB per process, 28 MB database), so it is >10x oversized on purpose.
This installer is venue-agnostic: it runs the same on Debian 12 or 13.

## 1. Get the code on the box

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/ideas-unlimited/trading-bot /opt/trading-bot
```

That clone is also the installer source: `install.sh` notices it is already
running from `/opt/trading-bot` and skips its copy step. A private repo would
need a deploy key — never a file copy.

## 2. Check the box first

```bash
cd /opt/trading-bot && sudo bash deploy/preflight.sh
```

Read-only: confirms the distro, RAM, disk, Python ≥ 3.11, and — the one that
matters most — that `api.binance.com` answers 200 from this IP. A 451 there
means the box is in a restricted location and no bot on it can trade.

## 3. Provision

```bash
cd /opt/trading-bot
bash deploy/install.sh
```

Installs Python venv + Postgres, creates the three databases
(`trading_common`, `trading_paper`, `trading_prod`), writes
`/opt/trading-bot/.env`, installs the bot and both web units, the forward
sleeve runner and the backup timer, enables the dashboards and the forward
loop, opens UFW (SSH only), starts fail2ban, and schedules a nightly `pg_dump`.
Re-running is safe; it never overwrites `.env`.

## 4. Add the Binance demo keys

Credentials are **per world**: the app loads `.env.<env>` first, then `.env`.
The demo pair belongs in `.env.paper` only.

```bash
sudo install -m 600 -o trader -g trader /dev/null /opt/trading-bot/.env.paper
sudo vi /opt/trading-bot/.env.paper   # BINANCE_DEMO_KEY / BINANCE_DEMO_SECRET
sudo systemctl enable --now tradingbot@btc tradingbot@eth tradingbot@sol
```

Until the placeholders are replaced the bots stay disabled on purpose; the
web terminal runs regardless.

**Prod refuses demo credentials** (`BinanceLiveClient` raises before reading
a key), so the 9001 dashboard shows *live venue not configured* rather than
a virtual balance. There is no live order path; adding one is a deliberate
code change with review, not a config flip or a copied key.

## 5. Updates are git pulls

The deployed tree is a clone, not a copy:

```bash
sudo bash /opt/trading-bot/deploy/update.sh
```

It fast-forwards `origin`, reinstalls the package, restarts the web service,
any running bot unit and the forward runner. Hand-editing files inside `/opt/trading-bot` is
what breaks this: change code locally, `git commit`, `git push`, pull here.

Editorial config stays outside the clone — `/etc/caddy/Caddyfile` (rendered
from `deploy/Caddyfile.template`) and `/etc/tradingbot/*.env` — because root
daemons read them and they belong to the box, not to the repository.

## 6. Reach the dashboards

Two hostnames, two worlds, one checkout:

| URL | World | Port | Database |
|---|---|---|---|
| `https://paper.139-99-236-166.sslip.io/` | PAPER - simulated fills | 9000 | `trading_paper` |
| `https://prod.139-99-236-166.sslip.io/` | PRODUCTION - real money | 9001 | `trading_prod` |
| `https://139-99-236-166.sslip.io/` | redirects to paper | - | - |

The env is rendered into the page server-side (title, a green PAPER or red
PRODUCTION banner, matching accent), so a screenshot always says which world
it is. Prod is read-only for bots and presets: create and tune on paper, then
promote with a manifest (`PROMOTION.md`). Each dashboard lists **only** the
strategies its `promotions/<env>/` manifests activate; the laptop runs
`BOT_LAB=1` (via `start_trader.cmd`) to see the whole registry while
researching.

Both processes bind `127.0.0.1` only; Caddy terminates TLS and basic auth,
and only 22/80/443 are open. Render the config with:

```bash
sudo bash deploy/public_web.sh 139-99-236-166.sslip.io sahil
```

Re-running reuses the existing login and password hash, so it cannot lock you
out; pass `WEB_PASSWORD='new-pass'` to rotate. Certificates are issued per
hostname (sslip.io resolves the subdomains to the same IP).

For a tunnel instead: `ssh -L 9000:127.0.0.1:9000 <user>@<ip>` (paper) or
`ssh -L 9001:127.0.0.1:9001 <user>@<ip>` (prod).

## Heartbeat (is the bot alive?)

A dead process and a quiet market look identical: no signals, no orders, no
output. Two layers turn that silence into information, and only the first one
knows *which* bot died.

### The watchdog: which bot died?

`tradingbot-watchdog.timer` runs `bot watchdog` every 5 minutes. It reads
`bots.last_heartbeat_at`, which every loop already writes on each poll:

- a bot silent past **10 minutes** (`WATCHDOG_STALE_MINUTES`) sends one alert
  through `/etc/tradingbot/alerts.env`, subject `BOT STALE: <bot>` with the
  symbol, last beat, age and the journal command. It repeats every 6 hours
  while the bot stays dead (`WATCHDOG_REPEAT_HOURS`), and `BOT RECOVERED:`
  follows when the heartbeat returns.
- while every running bot is fresh, it pings the dead-man URL from
  `/etc/tradingbot/watchdog.env`. With a stale bot it deliberately does not
  ping.

So one URL is honest here: green means "the watchdog ran and found no stale
bot", and the message names the strategy that broke.

### The external check: did the box die?

1. Create a free check at <https://healthchecks.io> (or self-host Uptime Kuma).
2. **Period 1h, grace 2h.** The watchdog pings every 5 minutes while the book
   is healthy, so green is current and a missed hour is a real outage.
3. Put its ping URL in `/etc/tradingbot/watchdog.env`, then:

   ```bash
   sudo systemctl start tradingbot-watchdog.service   # one pass now
   journalctl -u tradingbot-watchdog -n 20
   ```

**One URL for five bots is the trap.** A shared ping URL stays green while
*any* loop survives, so a single dead bot is invisible; a check also has one
period, so it cannot hold five deadlines. The watchdog avoids both by reading
per-bot heartbeats. If you want a per-bot check as well, give each env file
its own URL - never the same one. `run_bot.sh` still passes `HEARTBEAT_URL`
through from a bot's env file for that case.

A failed ping never stops the bot: monitoring downtime costs you an alert, not
a position.

## Trade alerts (email, Telegram, ntfy)

The live `tradingbot@<name>` bots send one message when they book a fill:
**TRADE OPENED** at entry and **TRADE CLOSED** with the realised P&L and
exit reason.  The subject is compact, e.g.
`capitulation_dip_uni:buy:4.8123: 4.8123/4.7000/5.1000` —
`strategy:side:price: entry/sl/tp` — and the body carries the details.

The forward runner is deliberately **silent by default**: it is a shadow
replay, not a trade, so it would duplicate the live message.  `bot forward
--alerts` opts in for a manual check; the systemd unit does not pass it, and
its cursor still prevents replaying history.

Alerts are off until at least one channel is configured.  Every live
`tradingbot@<name>` bot reads `/etc/tradingbot/alerts.env` (template:
`deploy/alerts.env.example`).  Each says `trade alerts: ...` (or `off`) at
startup.  `EnvironmentFile=-` means a missing file or an empty value is not
an error.

Edit the shared file, then restart the live bot(s) and verify:

```bash
sudo vi /etc/tradingbot/alerts.env
sudo -u trader /opt/trading-bot/.venv/bin/bot notify-test \
  --env-file /etc/tradingbot/alerts.env
sudo systemctl restart 'tradingbot@uni'   # repeat for each live bot you run
```

The `notify-test` command sends one message through every configured
channel and prints which ones are active.  It never places an order.

### Telegram (recommended for mobile)

1. Message `@BotFather`, `/newbot`, keep the token it returns.
2. Send the new bot any message (a bot cannot start a chat with you).
3. Open `https://api.telegram.org/bot<token>/getUpdates` and copy the
   numeric `chat.id`.
4. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `alerts.env`.

### ntfy (push, or email relay)

Install the ntfy app, subscribe to a long random topic, and set
`NTFY_URL=https://ntfy.sh/<topic>`.  Anyone who knows that URL can read the
messages, so treat the topic as a password; `NTFY_TOKEN` protects a
self-hosted or authenticated topic.  Trade sizes, symbols and P&L are
visible to the ntfy server.

The same publish can also forward to email: create a free ntfy.sh account,
verify `ALERT_EMAIL_TO` in Account, create an access token, and set
`NTFY_TOKEN` plus `NTFY_EMAIL=<verified address>`.  ntfy.sh blocks
anonymous email, so the token is required.  This is a relay, not an SMTP
client; the first message may still need marking as not-spam.

### Email (SMTP)

Set `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM` and `ALERT_EMAIL_TO` (comma-separated).  Port 587 uses
STARTTLS; `SMTP_SECURITY=ssl` for 465, `none` for a local relay.  Use an
app password, not the mailbox password, and expect the first message to
land in spam until the sender is trusted.  A daily-driver mailbox provider
is more reliable than a VPS's local MTA.

`alerts.env` is `root:trader` 0640 and is never committed.  When a token or
password is added, record it in the master secrets file outside the repo at
the same time.  An alert failure is logged as `[notify] <channel> failed`
and never stops the record.

## Operations

| Task | Command |
|---|---|
| Follow logs | `journalctl -u 'tradingbot@*' -u tradingbot-web -f` |
| One bot's state | `cat /var/lib/tradingbot/btc.json` |
| Stop everything | `systemctl stop 'tradingbot@*' tradingbot-web` |
| Add a bot | new `/etc/tradingbot/<name>.env` (template below), then `systemctl enable --now tradingbot@<name>` |
| Watchdog | `systemctl list-timers tradingbot-watchdog` · `journalctl -u tradingbot-watchdog -n 50` · `sudo -u trader /opt/trading-bot/.venv/bin/bot watchdog --dry-run` |
| Update | `sudo bash /opt/trading-bot/deploy/update.sh` |
| Restore a dump | `for db in trading_common trading_paper trading_prod; do zcat /var/backups/tradingbot/$db-<date>.sql.gz | runuser -u postgres -- psql $db; done` |

### Per-bot env file

`/etc/tradingbot/<name>.env` is `root:trader` 0640 and never committed.  The
live loop reads it through `deploy/run_bot.sh`; every value is quoted there, so
keep one `NAME=value` per line with no spaces around `=`.

```bash
# /etc/tradingbot/near.env -- one symbol per bot (the demo account is shared)
SYMBOL=NEAR/USDT
INTERVAL=4h
STRATEGY=swing_pullback_near
CAPITAL=1000
STATE=/var/lib/tradingbot/near.json
POLL=60
# The sleeve's backtest profile; the defaults are 0.01 / 0.95 / 0.10.
RISK_PER_TRADE=0.03
MAX_POSITION_PCT=0.95
MAX_DRAWDOWN_STOP=0.99
# HEARTBEAT_URL=https://hc-ping.com/<uuid>   # only for a per-bot check; the
#                                             # watchdog covers this bot already
```

The risk knobs fall back to the bot config's own defaults, so a bot that omits
them behaves exactly as before.  The per-symbol swing sleeves were measured at
3% risk with the account drawdown switch off: left at the 10% default they
would halt mid-year (NEAR's own history draws down 12.9% before recovering),
which is why both the bot env files and `tradingbot-forward.service` carry that
profile.  The ATR stop on each trade is the real risk control.
Every bot gets its own wallet (`--capital`), its own state file and its own
unit, but they share one demo account: two bots on the same symbol
would fight over the position. One symbol per bot.

## Memory budget (8 GB box)

Everything is bounded, so one runaway process cannot take the machine down:

| Unit | Measured | MemoryHigh (throttle) | MemoryMax (kill) |
|---|---|---|---|
| one bot | ~90 MB | 384M | 768M |
| web terminal | ~95 MB | 512M | 1G |
| nightly backup | ~20 MB | — | 256M |
| PostgreSQL | 512M buffers + ~50M | — | — |

Typical total is well under 2 GB; worst case with every ceiling hit is ~4.5 GB,
which still leaves the page cache room on 8 GB. Postgres is deliberately tuned
small (`shared_buffers=512MB`, `work_mem=16MB`, 30 connections) because the
database is 28 MB — buffer space spent there is buffer space the bar files
cannot use.

Both units run with single-threaded BLAS and a 2-arena malloc cap, which is
what keeps a Python process at ~90 MB instead of letting numpy reserve a
buffer per core.

**Research sweeps belong on the laptop, not here.** The server installs the
`[web]` extra only (no scikit-learn, no matplotlib), so a big sweep cannot be
started on the box by accident.

## Hardening already applied

- SSH-only ingress (UFW + fail2ban), web bound to loopback
- `ProtectSystem=strict`, `NoNewPrivileges`, `MemoryMax=1G` per service
- Postgres on `127.0.0.1`, database owned by an unprivileged role
- `.env` chmod 600, owned by the service user
- UTC clock, 2 GB swap, nightly backups with 14-day retention
