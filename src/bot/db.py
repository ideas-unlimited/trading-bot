"""Postgres persistence for the terminal: bots, presets, runs, paper trades, bars.

Requires the ``DATABASE_URL`` variable (e.g. in the git-ignored ``.env``):
    DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/trading_bot

The database is created on first connect if it does not exist, and the schema
is created with ``CREATE TABLE IF NOT EXISTS`` — nothing destructive.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .envdot import load_env

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as _exc:  # pragma: no cover
    psycopg = None
    dict_row = None
    _IMPORT_ERROR = _exc


DB_NAME = "trading_bot"

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id          serial PRIMARY KEY,
    name        text UNIQUE NOT NULL,
    kind        text NOT NULL,
    params      jsonb NOT NULL DEFAULT '{}',
    notes       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bots (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    symbol      text NOT NULL,
    timeframe   text NOT NULL,
    kind        text NOT NULL,
    params      jsonb NOT NULL DEFAULT '{}',
    config      jsonb NOT NULL DEFAULT '{}',
    mode        text NOT NULL DEFAULT 'paper',
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_run_at timestamptz
);
CREATE TABLE IF NOT EXISTS runs (
    id          serial PRIMARY KEY,
    bot_id      int REFERENCES bots(id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'backtest',
    symbol      text,
    timeframe   text,
    strategy    text,
    params      jsonb NOT NULL DEFAULT '{}',
    metrics     jsonb,
    equity      jsonb,
    buy_and_hold jsonb,
    trades      jsonb,
    bars        int,
    started_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS paper_trades (
    order_id   text PRIMARY KEY,
    symbol     text NOT NULL,
    side       text NOT NULL,
    qty        numeric NOT NULL,
    price      numeric NOT NULL,
    filled_at  timestamptz,
    bot_name   text NOT NULL DEFAULT '',
    synced_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bars (
    symbol     text NOT NULL,
    timeframe  text NOT NULL,
    ts         timestamptz NOT NULL,
    open       numeric NOT NULL,
    high       numeric NOT NULL,
    low        numeric NOT NULL,
    close      numeric NOT NULL,
    volume     numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS bars_lookup ON bars (symbol, timeframe, ts DESC);
"""


class DatabaseError(RuntimeError):
    pass


def database_url() -> str | None:
    """DATABASE_URL from the environment or .env, else ``None``."""
    load_env()
    url = os.environ.get("DATABASE_URL")
    if url and "/$" not in url and "YOUR_PASSWORD" not in url:
        return url
    return None


def is_available() -> bool:
    return psycopg is not None and database_url() is not None


def connect(*, create: bool = True) -> "psycopg.Connection":
    """Connect to ``trading_bot``, creating it (and the schema) if missing."""
    if psycopg is None:
        raise DatabaseError(
            "psycopg is not installed. Run:\n"
            '  .venv\\Scripts\\python -m pip install "psycopg[binary]"'
        )
    url = database_url()
    if not url:
        raise DatabaseError(
            "DATABASE_URL is not configured. Add to .env:\n"
            "  DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/trading_bot"
        )
    try:
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc) or not create:
            raise DatabaseError(f"Postgres connection failed: {exc}") from exc
        _create_database(url)
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    _ensure_schema(conn)
    return conn


def _create_database(url: str) -> None:
    base = url.rsplit("/", 1)[0]
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)
        ).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE {DB_NAME}')
    finally:
        admin.close()


def _ensure_schema(conn: "psycopg.Connection") -> None:
    conn.execute(SCHEMA)


# -- bars cache -------------------------------------------------------------
def upsert_bars(conn: "psycopg.Connection", symbol: str, timeframe: str, bars: list[Any]) -> int:
    from .data.feed import Bar

    rows = [
        (symbol, timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume)
        for b in bars
        if isinstance(b, Bar) or hasattr(b, "close")
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO bars (symbol, timeframe, ts, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, timeframe, ts) DO UPDATE
        SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, volume = EXCLUDED.volume
        """,
        rows,
    )
    return len(rows)


def load_bars(
    conn: "psycopg.Connection", symbol: str, timeframe: str,
    start: datetime | None = None, end: datetime | None = None,
) -> list[Any]:
    from .data.feed import Bar

    query = (
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE symbol = %s AND timeframe = %s"
    )
    args: list[Any] = [symbol, timeframe]
    if start:
        query += " AND ts >= %s"
        args.append(start)
    if end:
        query += " AND ts <= %s"
        args.append(end)
    query += " ORDER BY ts ASC"
    return [
        Bar(
            ts=row["ts"].astimezone(timezone.utc) if row["ts"].tzinfo else row["ts"].replace(tzinfo=timezone.utc),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in conn.execute(query, args).fetchall()
    ]


def bar_counts(conn: "psycopg.Connection") -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT symbol, timeframe, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
        "FROM bars GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    ).fetchall()
    return [
        {
            "symbol": r["symbol"], "timeframe": r["timeframe"], "n": int(r["n"]),
            "first_ts": r["first_ts"].isoformat(), "last_ts": r["last_ts"].isoformat(),
        }
        for r in rows
    ]


# -- presets ----------------------------------------------------------------
def save_preset(conn: "psycopg.Connection", name: str, kind: str, params: dict, notes: str = "") -> dict:
    row = conn.execute(
        """
        INSERT INTO strategies (name, kind, params, notes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE
        SET kind = EXCLUDED.kind, params = EXCLUDED.params, notes = EXCLUDED.notes
        RETURNING id, name, kind, params, notes
        """,
        (name, kind, json.dumps(params), notes),
    ).fetchone()
    return dict(row)


def list_presets(conn: "psycopg.Connection") -> list[dict]:
    return [
        dict(r) for r in conn.execute(
            "SELECT id, name, kind, params, notes FROM strategies ORDER BY created_at DESC"
        ).fetchall()
    ]


def delete_preset(conn: "psycopg.Connection", preset_id: int) -> bool:
    cur = conn.execute("DELETE FROM strategies WHERE id = %s", (preset_id,))
    return cur.rowcount > 0


# -- bots -------------------------------------------------------------------
def create_bot(conn: "psycopg.Connection", **fields: Any) -> dict:
    row = conn.execute(
        """
        INSERT INTO bots (name, symbol, timeframe, kind, params, config, mode)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, name, symbol, timeframe, kind, params, config, mode, created_at
        """,
        (
            fields["name"], fields["symbol"], fields["timeframe"], fields["kind"],
            json.dumps(fields.get("params") or {}), json.dumps(fields.get("config") or {}),
            fields.get("mode", "paper"),
        ),
    ).fetchone()
    bot = dict(row)
    bot["params"] = _json_load(bot["params"])
    bot["config"] = _json_load(bot["config"])
    return bot


def list_bots(conn: "psycopg.Connection") -> list[dict]:
    rows = conn.execute(
        """
        SELECT b.*, r.id AS last_run_id, r.metrics AS last_metrics
        FROM bots b LEFT JOIN LATERAL (
            SELECT id, metrics FROM runs WHERE bot_id = b.id ORDER BY started_at DESC LIMIT 1
        ) r ON TRUE
        ORDER BY b.created_at DESC
        """
    ).fetchall()
    out = []
    for r in rows:
        bot = dict(r)
        bot["params"] = _json_load(bot["params"])
        bot["config"] = _json_load(bot["config"])
        bot["last_metrics"] = _json_load(bot["last_metrics"])
        out.append(bot)
    return out


def get_bot(conn: "psycopg.Connection", bot_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM bots WHERE id = %s", (bot_id,)).fetchone()
    if not row:
        return None
    bot = dict(row)
    bot["params"] = _json_load(bot["params"])
    bot["config"] = _json_load(bot["config"])
    return bot


def delete_bot(conn: "psycopg.Connection", bot_id: int) -> bool:
    cur = conn.execute("DELETE FROM bots WHERE id = %s", (bot_id,))
    return cur.rowcount > 0


def save_run(conn: "psycopg.Connection", payload: dict) -> int:
    row = conn.execute(
        """
        INSERT INTO runs (bot_id, kind, symbol, timeframe, strategy, params,
                          metrics, equity, buy_and_hold, trades, bars)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            payload.get("bot_id"), payload.get("kind", "backtest"), payload.get("symbol"),
            payload.get("timeframe"), payload.get("strategy"), json.dumps(payload.get("params") or {}),
            json.dumps(_clean(payload.get("metrics"))), json.dumps(payload.get("equity") or []),
            json.dumps(payload.get("buy_and_hold") or []), json.dumps(payload.get("trades") or []),
            payload.get("bars"),
        ),
    ).fetchone()
    if payload.get("bot_id"):
        conn.execute(
            "UPDATE bots SET last_run_at = now() WHERE id = %s", (payload["bot_id"],)
        )
    return int(row["id"])


def list_runs(conn: "psycopg.Connection", bot_id: int | None = None, limit: int = 50) -> list[dict]:
    query = (
        "SELECT id, bot_id, kind, symbol, timeframe, strategy, metrics, bars, started_at "
        "FROM runs"
    )
    args: list[Any] = []
    if bot_id:
        query += " WHERE bot_id = %s"
        args.append(bot_id)
    query += " ORDER BY started_at DESC LIMIT %s"
    args.append(limit)
    return [_hydrate_run(dict(r)) for r in conn.execute(query, args).fetchall()]


def get_run(conn: "psycopg.Connection", run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
    return _hydrate_run(dict(row)) if row else None


def _hydrate_run(r: dict) -> dict:
    for key in ("metrics", "params"):
        r[key] = _json_load(r.get(key))
    r["started_at"] = r["started_at"].isoformat() if r.get("started_at") else None
    return r


# -- paper trades -----------------------------------------------------------
def upsert_paper_trades(conn: "psycopg.Connection", fills: list[dict]) -> int:
    if not fills:
        return 0
    conn.executemany(
        """
        INSERT INTO paper_trades (order_id, symbol, side, qty, price, filled_at, bot_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (order_id) DO NOTHING
        """,
        [
            (f["order_id"], f["symbol"], f["side"], f["qty"], f["price"], f["filled_at"], f.get("bot_name", ""))
            for f in fills
        ],
    )
    return len(fills)


def list_paper_trades(conn: "psycopg.Connection", limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT order_id, symbol, side, qty, price, filled_at, bot_name "
        "FROM paper_trades ORDER BY filled_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        {
            "order_id": r["order_id"], "symbol": r["symbol"], "side": r["side"],
            "qty": float(r["qty"]), "price": float(r["price"]),
            "filled_at": r["filled_at"].isoformat() if r["filled_at"] else None,
            "bot_name": r["bot_name"],
        }
        for r in rows
    ]


# -- helpers ----------------------------------------------------------------
def _json_load(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value if value is not None else ({} if value is None else value)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if obj != obj else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj
