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

- **Trading (Binance):** OVH VPS, **Sydney** — 4 vCPU, 8 GB RAM, 75 GB local
  disk, automated backup with 1 rotation. Binance returns HTTP 451 to US IPs,
  so the exchange-connected bots can only run here.
- **Paper/UI:** CometVPS "Standard 8GB", Seattle — 4 vCPU, 8 GB, 100 GB NVMe.

Either box needs ~1 GB of RAM and ~3 GB of disk for the whole book (measured
~90 MB per process, 28 MB database), so both are >10x oversized on purpose.
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
`/opt/trading-bot/.env`, installs the bot and both web units plus the backup
timer, enables the dashboards, UFW (SSH only) and fail2ban, and schedules a
nightly `pg_dump`.
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

It fast-forwards `origin`, reinstalls the package, restarts the web service
and any running bot unit. Hand-editing files inside `/opt/trading-bot` is
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

Same problem for every unattended bot: a dead process and a quiet market both
produce no signals, no orders and no output. Set up a dead-man's switch once
per bot and silence becomes information.

1. Create a free check at <https://healthchecks.io> (or self-host Uptime Kuma).
2. **Period 1h, grace 2h.** The bot pings every 30 minutes by default
   (`--heartbeat-every`), so one missed ping is jitter and two is an outage.
3. Put the check's ping URL in that bot's env file:

   ```bash
   echo 'HEARTBEAT_URL=https://hc-ping.com/<uuid>' >> /etc/tradingbot/btc.env
   systemctl restart tradingbot@btc
   ```

The ping fires after **every** poll cycle — including cycles with no new bar
and no trade — so it proves the loop is turning, not that the strategy is
busy. It also covers the box dying, which no local watchdog can report.

A failed ping never stops the bot: monitoring downtime costs you an alert, not
a position.

## Operations

| Task | Command |
|---|---|
| Follow logs | `journalctl -u 'tradingbot@*' -u tradingbot-web -f` |
| One bot's state | `cat /var/lib/tradingbot/btc.json` |
| Stop everything | `systemctl stop 'tradingbot@*' tradingbot-web` |
| Add a bot | new `/etc/tradingbot/<name>.env`, then `systemctl enable --now tradingbot@<name>` |
| Update | `sudo bash /opt/trading-bot/deploy/update.sh` |
| Restore a dump | `for db in trading_common trading_paper trading_prod; do zcat /var/backups/tradingbot/$db-<date>.sql.gz | runuser -u postgres -- psql $db; done` |

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
