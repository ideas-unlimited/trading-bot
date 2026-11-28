"""Environment separation: paper vs prod, common data, paths and ports.

BOT_ENV selects the world (paper by default). DATABASE_URL is the base URL;
only the database name changes: trading_common for market data,
trading_paper / trading_prod for bots, trades, equity, events and promotions.
An explicit DATABASE_URL_COMMON / _PAPER / _PROD wins over the derived name.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .envdot import load_env

ENVS = ("paper", "prod")
DEFAULT_ENV = "paper"
LAB_VAR = "BOT_LAB"
DB_COMMON = "trading_common"
DB_NAMES = {"paper": "trading_paper", "prod": "trading_prod"}
WEB_PORTS = {"paper": 9000, "prod": 9001}


def env_name() -> str:
    """BOT_ENV, validated; defaults to paper."""
    load_env()
    raw = (os.environ.get("BOT_ENV") or DEFAULT_ENV).strip().lower()
    if raw not in ENVS:
        raise ValueError(f"BOT_ENV must be one of {ENVS}, got {raw!r}")
    return raw


def lab_mode() -> bool:
    """True on the research laptop, where the whole registry is open.

    The server never sets BOT_LAB, so paper and prod there show only the
    strategies their promotion manifests activate. Lab is the one place
    an unpromoted strategy may appear, because that is where ideas live.
    """
    load_env()
    raw = (os.environ.get(LAB_VAR) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def database_name(kind: str = "env") -> str:
    """Database name for kind: env | common | paper | prod."""
    if kind == "env":
        kind = env_name()
    if kind == "common":
        return DB_COMMON
    if kind not in DB_NAMES:
        raise ValueError(f"unknown database kind {kind!r}")
    return DB_NAMES[kind]


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def database_url(kind: str = "env") -> str | None:
    """Connection URL for one database, or None when unconfigured."""
    load_env()
    if kind == "env":
        kind = env_name()
    override = {
        "common": "DATABASE_URL_COMMON",
        "paper": "DATABASE_URL_PAPER",
        "prod": "DATABASE_URL_PROD",
    }.get(kind)
    if override and os.environ.get(override):
        return os.environ[override]
    base = os.environ.get("DATABASE_URL")
    if not base or "/$" in base or "YOUR_PASSWORD" in base:
        return None
    return _with_db(base, database_name(kind))


def _root(override: str, unix: str, windows: str) -> Path:
    load_env()
    if os.environ.get(override):
        return Path(os.environ[override]).expanduser()
    return Path(windows) if os.name == "nt" else Path(unix)


def state_dir(name: str | None = None) -> Path:
    """Runtime state for one environment (bots json files, recorder cursor)."""
    return _root("BOT_STATE_DIR", "/var/lib/tradingbot", "runs") / (name or env_name())


def log_dir(name: str | None = None) -> Path:
    """Runtime logs: production artifacts, never workspace scratch."""
    return _root("BOT_LOG_DIR", "/var/log/tradingbot", "logs") / (name or env_name())


def web_port(name: str | None = None) -> int:
    load_env()
    raw = os.environ.get("BOT_WEB_PORT")
    if raw:
        return int(raw)
    return WEB_PORTS[name or env_name()]


def describe() -> dict:
    """Everything the CLI and the dashboard need to say which world it is."""
    name = env_name()
    return {
        "env": name,
        "lab": lab_mode(),
        "database": database_name(name),
        "common_database": DB_COMMON,
        "state_dir": str(state_dir(name)),
        "log_dir": str(log_dir(name)),
        "web_port": web_port(name),
    }
