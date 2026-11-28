"""Book watchdog: name the bot that died.

A shared dead-man's-switch URL cannot do that.  Every bot pinging one URL
keeps the check green as long as *any* loop survives, so one dead loop is
invisible; and a check has a single period, so it cannot hold five deadlines.
This module reads what the loops already write every poll -
``bots.last_heartbeat_at`` - and

* sends one alert (email / Telegram / ntfy, via :mod:`bot.notify`) naming the
  bot, how long it has been silent and when it last beat;
* pings the external dead-man check only while every running bot is fresh.

The external check therefore means "the book is alive"; the alert says which
sleeve is not.  One pass per invocation, run from a systemd timer.  A small
JSON state file remembers which bot is already in a stale episode, so a dead
loop pages once and then every ``repeat_after`` - not every five minutes.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_STALE_MINUTES = 10.0
DEFAULT_REPEAT_HOURS = 6.0
STATE_VERSION = 1


def format_age(seconds: float | None) -> str:
    """Compact duration for an alert: ``42s``, ``7m``, ``3h 04m``, ``2d 03h``."""
    if seconds is None or math.isinf(seconds):
        return "never"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{int(round(seconds))}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))}m"
    hours = minutes / 60.0
    if hours < 48:
        whole = int(hours)
        rest = int(round((hours - whole) * 60))
        return f"{whole}h {rest:02d}m"
    days = hours / 24.0
    whole = int(days)
    rest = int(round((days - whole) * 24))
    return f"{whole}d {rest:02d}h"


def running_bots(conn: Any) -> list[dict[str, Any]]:
    """Every bot the database believes is running, with its beat time.

    ``COALESCE(last_heartbeat_at, started_at)`` means a loop that has not
    completed a single poll yet is judged from its start, not called dead
    before it ever had the chance to beat.
    """
    rows = conn.execute(
        """
        SELECT id, name, symbol, timeframe, halted, started_at,
               COALESCE(last_heartbeat_at, started_at) AS beat,
               last_heartbeat_at
        FROM bots WHERE status = 'running' ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def age_seconds(beat: datetime | None, now: datetime) -> float:
    """Seconds since the last beat; infinite when there has never been one."""
    if beat is None:
        return math.inf
    return (now - beat).total_seconds()


def stale_bots(
    bots: list[dict[str, Any]], now: datetime, stale_after: timedelta
) -> list[dict[str, Any]]:
    """Running bots whose last beat is older than the threshold."""
    limit = stale_after.total_seconds()
    out: list[dict[str, Any]] = []
    for bot in bots:
        age = age_seconds(bot.get("beat"), now)
        if age > limit:
            out.append({**bot, "age_s": age})
    return out


def load_state(path: Path) -> dict[str, Any]:
    """Stale episodes from the previous pass; a broken file starts clean."""
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "bots": {}}
    if not isinstance(data, dict) or not isinstance(data.get("bots"), dict):
        return {"version": STATE_VERSION, "bots": {}}
    return {"version": STATE_VERSION,
            "bots": {str(key): value for key, value in data["bots"].items()}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write the episode file atomically; a torn write must not lose history."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", "utf-8")
    os.replace(tmp, path)


def _parse(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def alert_message(bot: dict[str, Any], now: datetime) -> tuple[str, str]:
    """Subject and body for one stale bot.  The subject names it."""
    subject = f"BOT STALE: {bot['name']}"
    lines = [
        "NO HEARTBEAT",
        f"{bot['name']}  {bot['symbol']}  {bot['timeframe']}",
        f"last beat   {_utc(bot.get('last_heartbeat_at'))}"
        f"  ({format_age(bot.get('age_s'))} ago)",
        f"started     {_utc(bot.get('started_at'))}",
        f"halted      {'yes' if bot.get('halted') else 'no'}",
        "",
        "Every loop writes bots.last_heartbeat_at each poll. This one has been",
        "silent past the threshold, so it is not trading.",
        "",
        "check  journalctl -u 'tradingbot@*' -n 100",
        "       systemctl status 'tradingbot@*'",
        "",
        "Demo world - no real money moved.",
    ]
    return subject, "\n".join(lines)


def recovery_message(
    bot: dict[str, Any], episode: dict[str, Any], now: datetime
) -> tuple[str, str]:
    """Subject and body for a bot whose heartbeat came back."""
    subject = f"BOT RECOVERED: {bot['name']}"
    first = _parse(episode.get("first_stale_at"))
    beat = bot.get("beat")
    silent = (beat - first).total_seconds() if (beat and first) else None
    lines = [
        "HEARTBEAT RESUMED",
        f"{bot['name']}  {bot['symbol']}  {bot['timeframe']}",
        f"last beat   {_utc(beat)}",
        f"was silent  {format_age(silent)}",
        "",
        "The loop is turning again; check the journal if it restarted by itself.",
        "",
        "Demo world - no real money moved.",
    ]
    return subject, "\n".join(lines)


def run_once(
    conn: Any,
    *,
    state_path: Path,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=DEFAULT_STALE_MINUTES),
    repeat_after: timedelta = timedelta(hours=DEFAULT_REPEAT_HOURS),
    heartbeat: Any | None = None,
    notifier: Any | None = None,
    persist: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """One pass: alert the stale, clear the recovered, ping if all fresh."""
    now = now or datetime.now(timezone.utc)
    bots = running_bots(conn)
    stale = stale_bots(bots, now, stale_after)
    state = load_state(state_path) if persist else {
        "version": STATE_VERSION, "bots": {},
    }
    episodes: dict[str, Any] = state["bots"]
    stale_ids = {str(bot["id"]) for bot in stale}

    alerted = 0
    delivered = 0
    for bot in stale:
        key = str(bot["id"])
        episode = episodes.get(key)
        previous = _parse((episode or {}).get("last_alert_at"))
        due = previous is None or (now - previous) >= repeat_after
        if due:
            subject, body = alert_message(bot, now)
            ok = False
            if notifier is not None:
                ok = bool(notifier.send(subject, body))
            alerted += 1
            delivered += 1 if ok else 0
            log(f"[watchdog] stale: {bot['name']} silent {format_age(bot['age_s'])}"
                + (" - alert sent" if ok else " - alert NOT delivered"))
        first = (episode or {}).get("first_stale_at")
        if not first:
            first = (bot.get("beat") or now).isoformat(timespec="seconds")
        episodes[key] = {
            "name": bot["name"],
            "symbol": bot["symbol"],
            "first_stale_at": first,
            "last_alert_at": now.isoformat(timespec="seconds"),
        }

    recovered = 0
    for key, episode in list(episodes.items()):
        if key in stale_ids:
            continue
        bot = next((item for item in bots if str(item["id"]) == key), None)
        if bot is None:
            log(f"[watchdog] {episode.get('name')} no longer running; episode cleared")
        else:
            subject, body = recovery_message(bot, episode, now)
            if notifier is not None:
                notifier.send(subject, body)
            recovered += 1
            log(f"[watchdog] recovered: {bot['name']}")
        del episodes[key]

    pinged = False
    if stale:
        log(f"[watchdog] not pinging the dead-man check: {len(stale)} stale bot(s)")
    elif heartbeat is None or not getattr(heartbeat, "enabled", False):
        log("[watchdog] no dead-man URL configured")
    else:
        pinged = bool(heartbeat.ping(force=True))
        if not pinged:
            log("[watchdog] dead-man ping failed")

    state["bots"] = episodes
    if persist:
        save_state(state_path, state)

    return {
        "running": len(bots),
        "stale": [
            {"id": bot["id"], "name": bot["name"], "symbol": bot["symbol"],
             "age_s": bot["age_s"]}
            for bot in stale
        ],
        "alerted": alerted,
        "delivered": delivered,
        "recovered": recovered,
        "pinged": pinged,
        "channels": getattr(notifier, "describe", lambda: "")()
        if notifier is not None else "",
    }


__all__ = [
    "DEFAULT_REPEAT_HOURS",
    "DEFAULT_STALE_MINUTES",
    "age_seconds",
    "alert_message",
    "format_age",
    "load_state",
    "recovery_message",
    "run_once",
    "running_bots",
    "save_state",
    "stale_bots",
]
