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

#: market data, shared by both environments
SCHEMA_COMMON = """
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
CREATE TABLE IF NOT EXISTS book_snapshots (
    symbol         text NOT NULL,
    ts             timestamptz NOT NULL,
    last_update_id bigint NOT NULL DEFAULT 0,
    bids           jsonb NOT NULL,
    asks           jsonb NOT NULL,
    PRIMARY KEY (symbol, ts)
);
CREATE INDEX IF NOT EXISTS book_snapshots_lookup ON book_snapshots (symbol, ts DESC);
"""

#: per-environment tables: bots, trades, equity, events, promotions
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
ALTER TABLE bots ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'paper';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'registered';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS stopped_at timestamptz;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamptz;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS capital numeric;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS strategy_name text NOT NULL DEFAULT '';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS params_hash text NOT NULL DEFAULT '';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS git_tag text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS bot_trades (
    id              bigserial PRIMARY KEY,
    bot_id          int REFERENCES bots(id) ON DELETE CASCADE,
    symbol          text NOT NULL,
    side            text NOT NULL,
    qty             numeric NOT NULL,
    price           numeric NOT NULL,
    fee             numeric NOT NULL DEFAULT 0,
    realized_pnl    numeric,
    opened_at       timestamptz,
    closed_at       timestamptz,
    closed_by       text NOT NULL DEFAULT '',
    order_id        text NOT NULL DEFAULT '',
    client_order_id text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS bot_trades_lookup ON bot_trades (bot_id, id DESC);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    bot_id       int REFERENCES bots(id) ON DELETE CASCADE,
    ts           timestamptz NOT NULL,
    equity       numeric NOT NULL,
    cash         numeric,
    position_qty numeric,
    mark_price   numeric,
    PRIMARY KEY (bot_id, ts)
);
CREATE TABLE IF NOT EXISTS events (
    id      bigserial PRIMARY KEY,
    bot_id  int,
    ts      timestamptz NOT NULL DEFAULT now(),
    level   text NOT NULL DEFAULT 'info',
    kind    text NOT NULL,
    message text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_lookup ON events (bot_id, id DESC);
CREATE TABLE IF NOT EXISTS promotions (
    id          bigserial PRIMARY KEY,
    strategy    text NOT NULL,
    env         text NOT NULL,
    action      text NOT NULL,
    params_hash text NOT NULL DEFAULT '',
    capital     numeric,
    checks      jsonb NOT NULL DEFAULT '[]',
    override    boolean NOT NULL DEFAULT false,
    reason      text NOT NULL DEFAULT '',
    actor       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS promotions_lookup ON promotions (strategy, env, id DESC);
"""


class DatabaseError(RuntimeError):
    pass


def database_url(kind: str = "env") -> str | None:
    """URL for one database: kind is env | common | paper | prod."""
    from . import envcfg

    return envcfg.database_url(kind)


def is_available() -> bool:
    return psycopg is not None and database_url() is not None


def connect(*, create: bool = True, kind: str = "env") -> "psycopg.Connection":
    """Connect to the common or environment database, creating it if missing."""
    if psycopg is None:
        raise DatabaseError(
            "psycopg is not installed. Run:\n"
            '  .venv\\Scripts\\python -m pip install "psycopg[binary]"'
        )
    url = database_url(kind)
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
        try:
            _create_database(url)
            conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        except psycopg.Error as exc:
            raise DatabaseError(
                f"database is missing and could not be created ({exc}); "
                "create it once as the postgres superuser: "
                "psql -U postgres -f deploy/create_databases.sql"
            ) from exc
    try:
        _ensure_schema(conn, kind)
    except psycopg.Error as exc:
        conn.close()
        raise DatabaseError(f"schema setup failed: {exc}") from exc
    return conn


def _create_database(url: str) -> None:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    name = parts.path.lstrip("/") or DB_NAME
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    admin = psycopg.connect(admin_url, autocommit=True)
    try:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()


def _ensure_schema(conn: "psycopg.Connection", kind: str = "env") -> None:
    conn.execute(SCHEMA_COMMON if kind == "common" else SCHEMA)


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
    with conn.cursor() as cur:
        cur.executemany(
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


def prune_bars(conn: "psycopg.Connection", keep_days: int = 730) -> int:
    """Delete bars older than ``keep_days``; returns the number removed."""
    cur = conn.execute(
        "DELETE FROM bars WHERE ts < now() - make_interval(days => %s)",
        (keep_days,),
    )
    return cur.rowcount


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




# -- order-book depth (production snapshots, recorded forward only) ---------
def insert_book_snapshot(conn: "psycopg.Connection", snap: Any) -> None:
    """Persist one depth snapshot; duplicate (symbol, ts) is ignored."""
    conn.execute(
        """
        INSERT INTO book_snapshots (symbol, ts, last_update_id, bids, asks)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (symbol, ts) DO NOTHING
        """,
        (
            snap.symbol,
            snap.ts,
            int(getattr(snap, "last_update_id", 0)),
            json.dumps([[l.price, l.qty] for l in snap.bids]),
            json.dumps([[l.price, l.qty] for l in snap.asks]),
        ),
    )


def load_book_snapshots(conn: "psycopg.Connection", symbol: str, limit: int = 1) -> list[dict[str, Any]]:
    """Newest-first snapshots for one symbol, bids/asks decoded to floats."""
    rows = conn.execute(
        "SELECT ts, last_update_id, bids, asks FROM book_snapshots "
        "WHERE symbol = %s ORDER BY ts DESC LIMIT %s",
        (symbol, int(limit)),
    ).fetchall()
    return [
        {"ts": r["ts"], "last_update_id": int(r["last_update_id"]),
         "bids": [[float(p), float(q)] for p, q in r["bids"]],
         "asks": [[float(p), float(q)] for p, q in r["asks"]]}
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
    with conn.cursor() as cur:
        cur.executemany(
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

# -- environment separation -------------------------------------------------
def connect_common(*, create: bool = True) -> "psycopg.Connection":
    """The market-data database shared by paper and prod (bars, depth)."""
    return connect(create=create, kind="common")


# -- bot lifecycle ----------------------------------------------------------
def register_bot(
    conn: "psycopg.Connection",
    name: str,
    *,
    symbol: str,
    timeframe: str,
    strategy_name: str,
    params: dict[str, Any],
    capital: float,
    env: str,
    git_tag: str = "",
    config: dict[str, Any] | None = None,
) -> dict:
    """Insert a running bot row and return it."""
    from .promotions import params_hash

    row = conn.execute(
        """
        INSERT INTO bots (name, symbol, timeframe, kind, params, config, mode, env,
                          status, started_at, capital, strategy_name, params_hash, git_tag)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', now(), %s, %s, %s, %s)
        RETURNING id, name, symbol, timeframe, status, started_at, capital
        """,
        (name, symbol, timeframe, strategy_name, json.dumps(params),
         json.dumps(config or {}), env, env, float(capital),
         strategy_name, params_hash(params), git_tag),
    ).fetchone()
    bot = dict(row)
    if bot.get("capital") is not None:
        bot["capital"] = float(bot["capital"])
    return bot


def bot_heartbeat(conn: "psycopg.Connection", bot_id: int) -> None:
    conn.execute(
        "UPDATE bots SET last_heartbeat_at = now(), status = 'running' WHERE id = %s", (bot_id,)
    )


def stop_bot(conn: "psycopg.Connection", bot_id: int, status: str = "stopped") -> None:
    conn.execute(
        "UPDATE bots SET status = %s, stopped_at = now() WHERE id = %s", (status, bot_id)
    )


def record_trade(
    conn: "psycopg.Connection",
    bot_id: int,
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    fee: float = 0.0,
    realized_pnl: float | None = None,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
    closed_by: str = "",
    order_id: str = "",
    client_order_id: str = "",
) -> int:
    row = conn.execute(
        """
        INSERT INTO bot_trades (bot_id, symbol, side, qty, price, fee, realized_pnl,
                                opened_at, closed_at, closed_by, order_id, client_order_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (bot_id, symbol, side, float(qty), float(price), float(fee), realized_pnl,
         opened_at, closed_at, closed_by, order_id, client_order_id),
    ).fetchone()
    return int(row["id"])


def record_equity(
    conn: "psycopg.Connection",
    bot_id: int,
    *,
    equity: float,
    cash: float | None = None,
    position_qty: float | None = None,
    mark_price: float | None = None,
    ts: datetime | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO equity_snapshots (bot_id, ts, equity, cash, position_qty, mark_price)
        VALUES (%s, COALESCE(%s, now()), %s, %s, %s, %s)
        ON CONFLICT (bot_id, ts) DO NOTHING
        """,
        (bot_id, ts, float(equity), cash, position_qty, mark_price),
    )


def log_event(
    conn: "psycopg.Connection",
    bot_id: int | None,
    kind: str,
    message: str = "",
    *,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
    """Never pass secrets: only clientOrderId and venue error codes."""
    conn.execute(
        "INSERT INTO events (bot_id, level, kind, message, payload) VALUES (%s, %s, %s, %s, %s)",
        (bot_id, level, kind, message, json.dumps(payload or {})),
    )


def bot_lifecycle(conn: "psycopg.Connection", limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, symbol, timeframe, strategy_name, env, status, capital,
               started_at, stopped_at, last_heartbeat_at, git_tag,
               (SELECT COUNT(*) FROM bot_trades t WHERE t.bot_id = bots.id) AS trades,
               (SELECT COALESCE(SUM(t.realized_pnl), 0) FROM bot_trades t WHERE t.bot_id = bots.id) AS realized,
               (SELECT equity FROM equity_snapshots e WHERE e.bot_id = bots.id ORDER BY ts DESC LIMIT 1) AS equity
        FROM bots ORDER BY started_at DESC NULLS LAST, id DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        for key in ("capital", "realized", "equity"):
            if row.get(key) is not None:
                row[key] = float(row[key])
        out.append(row)
    return out


def latest_bot(conn: "psycopg.Connection", name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM bots WHERE name = %s ORDER BY id DESC LIMIT 1", (name,)
    ).fetchone()
    return dict(row) if row else None


# -- promotions audit -------------------------------------------------------
def create_promotion(
    conn: "psycopg.Connection",
    *,
    strategy: str,
    env: str,
    action: str,
    params_hash: str = "",
    capital: float | None = None,
    checks: list[dict] | None = None,
    override: bool = False,
    reason: str = "",
    actor: str = "",
) -> int:
    row = conn.execute(
        """
        INSERT INTO promotions (strategy, env, action, params_hash, capital, checks,
                                override, reason, actor)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (strategy, env, action, params_hash, capital, json.dumps(checks or []),
         bool(override), reason, actor),
    ).fetchone()
    return int(row["id"])


def list_promotions(conn: "psycopg.Connection", limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, strategy, env, action, params_hash, capital, override, reason,
               actor, created_at, checks
        FROM promotions ORDER BY id DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        row["checks"] = _json_load(row.get("checks"))
        row["capital"] = float(row["capital"]) if row.get("capital") is not None else None
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
        out.append(row)
    return out

