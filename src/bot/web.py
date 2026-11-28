"""Local web dashboard for the bot: backtests, timeframe scans, paper account.

    python -m bot web --port 8000

Serves a single-page UI (``web/index.html``) plus a small JSON API.  Bars
come from Binance (production klines, Postgres-cached); the account tab reads
the demo wallet.  Same honesty rules as the rest of the project: the only
order-related endpoint belongs to the paper trader, and nothing here can
reach a live broker.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .exchange.binance import BrokerError
from .exchange.live_client import BinanceLiveClient
from .backtest import run_backtest
from .config import BotConfig, CostConfig, RiskConfig
from .envcfg import describe as env_describe, env_name
from .data.feed import generate_synthetic
from .metrics import infer_bars_per_year
from .scanlib import load_scan_bars, make_config, run_scan
from .strategy import REGISTRY, build
from . import db as store

WEB_DIR = Path(__file__).resolve().parent / "web"

# The prod dashboard is read-only: a strategy reaches prod by promotion
# package, never by clicking "create bot" against the live database.
READ_ONLY_ENVS = ("prod",)


def _read_only() -> bool:
    try:
        return env_name() in READ_ONLY_ENVS
    except ValueError:
        return True


def render_index() -> bytes:
    """Serve the SPA with its world burned into the page.

    The same HTML runs on both ports, so the env label, banner and colour
    theming are injected here; a cached page can never show the wrong world.
    """
    env = env_name()
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    if env == "prod":
        label = "PRODUCTION"
        note = "REAL MONEY · live orders when the broker path is enabled"
        foot = "PRODUCTION<br>real money"
    else:
        label = "PAPER"
        note = "simulated fills · no real money"
        foot = "PAPER ONLY<br>no real orders possible"
    tokens = {
        "{{ENV}}": env,
        "{{ENV_NAME}}": f"{env} · terminal",
        "{{ENV_LABEL}}": label,
        "{{ENV_NOTE}}": note,
        "{{ENV_FOOT}}": foot,
        "{{ENV_JSON}}": json.dumps(env),
    }
    for token, value in tokens.items():
        html = html.replace(token, value)
    return html.encode("utf-8")

# Curated ticker lists for the terminal dropdowns.  Crypto pairs come from
# Binance; the other lists exist only as TradingView research feeds
# (tvDatafeed, unofficial) and cannot be paper-traded.
SYMBOLS = {
    "crypto": [
        # Binance spot pairs.  symbol is the display form ("BTC/USDT"); tv is
        # the TradingView ticker the research feed uses.  No stock pairs:
        # this desk trades spot crypto only.
        {"symbol": "BTC/USDT", "tv": "BINANCE:BTCUSDT"},
        {"symbol": "ETH/USDT", "tv": "BINANCE:ETHUSDT"},
        {"symbol": "SOL/USDT", "tv": "BINANCE:SOLUSDT"},
        {"symbol": "XRP/USDT", "tv": "BINANCE:XRPUSDT"},
        {"symbol": "LINK/USDT", "tv": "BINANCE:LINKUSDT"},
        {"symbol": "DOGE/USDT", "tv": "BINANCE:DOGEUSDT"},
        {"symbol": "AVAX/USDT", "tv": "BINANCE:AVAXUSDT"},
        {"symbol": "LTC/USDT", "tv": "BINANCE:LTCUSDT"},
        {"symbol": "BCH/USDT", "tv": "BINANCE:BCHUSDT"},
        {"symbol": "DOT/USDT", "tv": "BINANCE:DOTUSDT"},
        {"symbol": "BNB/USDT", "tv": "BINANCE:BNBUSDT"},
        {"symbol": "HYPE/USDT", "tv": "HYPE:HYPEUSDT"},
        {"symbol": "UNI/USDT", "tv": "BINANCE:UNIUSDT"},
        {"symbol": "JUP/USDT", "tv": "BINANCE:JUPUSDT"},
        {"symbol": "PYTH/USDT", "tv": "BINANCE:PYTHUSDT"},
        {"symbol": "CAKE/USDT", "tv": "BINANCE:CAKEUSDT"},
        {"symbol": "CC/USDC", "tv": "MEXC:CCUSDC"},
    ],
    "commodities": [
        {"symbol": "XAUT/USD", "tv": "BITFINEX:XAUTUSD", "label": "Tether Gold"},
        {"symbol": "XAG/USD", "tv": "OANDA:XAGUSD", "label": "Silver spot"},
        {"symbol": "USOIL", "tv": "TVC:USOIL", "label": "WTI Crude Oil"},
    ],
    "indices": [
        {"symbol": "BTC.D", "tv": "CRYPTOCAP:BTC.D", "label": "Bitcoin dominance"},
    ],
}

MAX_CURVE_POINTS = 600
MAX_TRADES_SHOWN = 50


def _jsonify(value: Any) -> Any:
    """Recursively convert NaN/None to null and datetimes to ISO strings."""
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


def api_strategies() -> dict[str, Any]:
    return {
        name: {"params": list(cls.PARAMS), "doc": (cls.__doc__ or "").strip().splitlines()[0]}
        for name, cls in sorted(REGISTRY.items())
    }


def _load_bars_for_api(payload: dict[str, Any]) -> list[Any]:
    source = payload.get("source", "binance")
    symbol = payload.get("symbol", "BTC/USDT")
    interval = payload.get("interval", "1d")
    days = int(payload.get("days") or 365)
    src, fetch = _resolve_feed(symbol, source)
    return load_scan_bars(fetch, interval, source=src, days=days, cache_key=symbol)


def _ohlc_payload(bars: list[Any], max_points: int = 400) -> list[list[float]]:
    """Downsampled OHLC rows [ts, open, high, low, close] for candlestick charts."""
    step = max(1, len(bars) // max_points)
    picked = bars[::step]
    if picked and picked[-1] is not bars[-1]:
        picked.append(bars[-1])
    return [
        [b.ts.isoformat(), b.open, b.high, b.low, b.close]
        for b in picked
    ]


def api_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one backtest and serialize metrics, equity curve and trades."""
    strategy_name = payload.get("strategy", "sma_cross")
    params = payload.get("params") or {}
    try:
        strategy = build(strategy_name, **params)
    except (TypeError, ValueError) as exc:
        return {"error": f"bad strategy parameters: {exc}"}

    try:
        bars = _load_bars_for_api(payload)
    except BrokerError as exc:
        return {"error": f"Binance error: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if len(bars) < strategy.warmup + 5:
        return {"error": f"only {len(bars)} bars fetched; not enough for warmup"}

    config_overrides = {
        k: payload.get(k)
        for k in (
            "initial_cash", "fee_bps", "slippage_bps",
            "risk_per_trade", "max_position_pct", "max_drawdown_stop",
        )
        if payload.get(k) is not None
    }
    config = make_config(bars, **config_overrides)
    result = run_backtest(bars, strategy, config)

    curve = result.equity_curve
    close = result.close
    step = max(1, len(curve) // MAX_CURVE_POINTS)
    equity_points = [
        [idx.isoformat() if hasattr(idx, "isoformat") else str(idx), float(curve.iloc[k])]
        for k, idx in enumerate(curve.index)
        if k % step == 0
    ]
    if (len(curve) - 1) % step != 0:
        last_idx = curve.index[-1]
        equity_points.append([last_idx.isoformat(), float(curve.iloc[-1])])
    bh_step = max(1, len(close) // MAX_CURVE_POINTS)
    bh_points = [
        [idx.isoformat(), float(close.iloc[k])]
        for k, idx in enumerate(close.index)
        if k % bh_step == 0
    ]

    trades = [
        {
            "entry_ts": t.entry_ts.isoformat(),
            "exit_ts": t.exit_ts.isoformat(),
            "qty": t.qty,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "return_pct": t.return_pct,
            "reason_open": t.reason_open,
            "reason_close": t.reason_close,
        }
        for t in result.trades[-MAX_TRADES_SHOWN:]
    ]

    return _jsonify(
        {
            "strategy": result.strategy,
            "symbol": payload.get("symbol"),
            "interval": payload.get("interval"),
            "bars": len(bars),
            "initial_equity": result.initial_equity,
            "final_equity": result.final_equity,
            "metrics": result.metrics,
            "equity_curve": equity_points,
            "buy_and_hold_curve": bh_points,
            "ohlc": _ohlc_payload(bars),
            "trades": trades,
            "halted_reason": result.halted_reason,
        }
    )


def _symbol_meta(canonical: str) -> dict[str, Any] | None:
    for entries in SYMBOLS.values():
        for entry in entries:
            if entry["symbol"] == canonical:
                return entry
    return None


def _resolve_feed(canonical: str, source: str) -> tuple[str, str]:
    """Map a dropdown symbol to (feed, fetch_symbol).

    TradingView symbols become ``EXCHANGE:TICKER`` using the catalog mapping;
    unknown/custom symbols with an ``EXCHANGE:`` prefix pass through as-is.
    """
    if source == "tv":
        tv = (_symbol_meta(canonical) or {}).get("tv")
        if tv:
            return "tv", tv
        if ":" in canonical:
            return "tv", canonical
        return "tv", f"BINANCE:{canonical}"
    return source, canonical


def api_scan(payload: dict[str, Any]) -> dict[str, Any]:
    symbols = [s.strip() for s in (payload.get("symbols") or ["BTC/USDT"]) if s.strip()]
    timeframes = [t.strip() for t in (payload.get("timeframes") or ["1d"]) if t.strip()]
    strategies = [s.strip() for s in (payload.get("strategies") or ["sma_cross"]) if s.strip()]
    source = payload.get("source", "binance")
    config_overrides = {
        k: payload.get(k)
        for k in (
            "initial_cash", "fee_bps", "slippage_bps",
            "risk_per_trade", "max_position_pct", "max_drawdown_stop",
        )
        if payload.get(k) is not None
    }
    try:
        rows = run_scan_rows(
            symbols, timeframes, strategies,
            source=source, days=int(payload.get("days") or 365),
            params=payload.get("params") or {},
            config_overrides=config_overrides,
        )
    except BrokerError as exc:
        return {"error": f"Binance error: {exc}"}
    return _jsonify({"rows": rows})


def run_scan_rows(
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    *,
    source: str,
    days: int,
    params: dict[str, Any],
    config_overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    """Scan across symbols, resolving each to its feed (Binance or TV)."""
    feeds = {s: _resolve_feed(s, source) for s in symbols}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        src, fetch = feeds[symbol]
        for tf in timeframes:
            try:
                bars = load_scan_bars(fetch, tf, source=src, days=days, n_bars=10_000,
                                      cache_key=symbol)
            except Exception as exc:  # noqa: BLE001 - report per-combination failures
                rows.append({"symbol": symbol, "timeframe": tf, "strategy": "-",
                             "error": str(exc)[:160]})
                continue
            if len(bars) < 60:
                rows.append({"symbol": symbol, "timeframe": tf, "strategy": "-",
                             "error": f"only {len(bars)} bars fetched"})
                continue
            config = make_config(bars, **config_overrides)
            for name in strategies:
                try:
                    strategy = build(name, **params)
                except (TypeError, ValueError) as exc:
                    rows.append({"symbol": symbol, "timeframe": tf, "strategy": name,
                                 "error": str(exc)})
                    continue
                result = run_backtest(bars, strategy, config)
                m = result.metrics
                rows.append(
                    {
                        "symbol": symbol, "timeframe": tf, "strategy": name, "bars": len(bars),
                        "total_return": m.get("total_return"), "buy_and_hold": m.get("buy_and_hold"),
                        "sharpe": m.get("sharpe"), "sortino": m.get("sortino"),
                        "max_drawdown": m.get("max_drawdown"), "calmar": m.get("calmar"),
                        "trades": int(m.get("trades") or 0), "win_rate": m.get("win_rate"),
                        "profit_factor": m.get("profit_factor"), "halted": bool(result.halted_reason),
                    }
                )

    def sort_key(row: dict[str, Any]) -> float:
        sharpe = row.get("sharpe")
        return float(sharpe) if isinstance(sharpe, (int, float)) and sharpe == sharpe else float("-inf")

    return sorted(rows, key=sort_key, reverse=True)


def api_account() -> dict[str, Any]:
    try:
        client = BinanceLiveClient()
        acct = client.get_account()
        positions = client.get_positions()
    except BrokerError as exc:
        return {"error": f"Binance error: {exc}"}
    return _jsonify(
        {
            "account_number": acct.get("account_number"),
            "status": acct.get("status"),
            "equity": float(acct.get("equity") or 0),
            "cash": float(acct.get("cash") or 0),
            "buying_power": float(acct.get("buying_power") or 0),
            "positions": [
                {
                    "symbol": p.get("symbol"),
                    "qty": float(p.get("qty") or 0),
                    "avg_entry_price": float(p.get("avg_entry_price") or 0),
                    "unrealized_pl": float(p.get("unrealized_pl") or 0),
                }
                for p in positions
            ],
        }
    )


# -- terminal: symbols, bots, presets, runs, trades, data --------------------
TIMEFRAME_ALIASES = {"1h": "1Hour", "4h": "4Hour", "6h": "6h", "1d": "1Day", "1w": "1Week"}


def api_symbols() -> dict[str, Any]:
    return SYMBOLS


def _db_conn() -> Any:
    """Return a live connection or raise ValueError with a setup message."""
    try:
        return store.connect()
    except store.DatabaseError as exc:
        raise ValueError(str(exc)) from exc


def api_bots_list() -> dict[str, Any]:
    conn = _db_conn()
    try:
        return _jsonify({"bots": store.list_bots(conn)})
    finally:
        conn.close()


def api_bots_create(payload: dict[str, Any]) -> dict[str, Any]:
    if _read_only():
        return {"error": "prod is read-only: create bots on paper, then promote"}
    name = (payload.get("name") or "").strip()
    symbol = (payload.get("symbol") or "").strip()
    timeframe = (payload.get("timeframe") or "").strip()
    kind = (payload.get("kind") or "sma_cross").strip()
    if not name or not symbol or timeframe not in TIMEFRAME_ALIASES or kind not in REGISTRY:
        return {"error": "name, symbol, timeframe and a valid strategy kind are required"}
    params = payload.get("params") or {}
    try:
        build(kind, **params)  # validate before saving
    except (TypeError, ValueError) as exc:
        return {"error": f"bad strategy parameters: {exc}"}
    config = {
        k: payload.get(k)
        for k in (
            "initial_cash", "fee_bps", "slippage_bps",
            "risk_per_trade", "max_position_pct", "max_drawdown_stop",
        )
        if payload.get(k) is not None
    }
    conn = _db_conn()
    try:
        bot = store.create_bot(
            conn, name=name, symbol=symbol, timeframe=timeframe,
            kind=kind, params=params, config=config, mode=payload.get("mode", "paper"),
        )
        return _jsonify({"bot": bot})
    finally:
        conn.close()


def api_bots_delete(bot_id: int) -> dict[str, Any]:
    if _read_only():
        return {"error": "prod is read-only: demote on paper, then promote"}
    conn = _db_conn()
    try:
        return {"deleted": store.delete_bot(conn, bot_id)}
    finally:
        conn.close()


def _bars_for_bot(bot: dict[str, Any], days: int = 730) -> list[Any]:
    """Bars for a bot: Postgres cache when rich enough, else Binance."""
    symbol, tf = bot["symbol"], bot["timeframe"]
    start = datetime.now(timezone.utc) - timedelta(days=days)
    conn = None
    if store.is_available():
        try:
            conn = store.connect_common()
            cached = store.load_bars(conn, symbol, tf, start=start)
            if len(cached) >= 200:
                conn.close()
                return cached
        except store.DatabaseError:
            conn = None
    try:
        bars = load_scan_bars(symbol, tf, source="binance", days=days)
        if conn is not None:
            try:
                store.upsert_bars(conn, symbol, tf, bars)
            except store.DatabaseError:
                pass
        return bars
    finally:
        if conn is not None:
            conn.close()


def api_bot_run(bot_id: int) -> dict[str, Any]:
    conn = _db_conn()
    try:
        bot = store.get_bot(conn, bot_id)
    finally:
        conn.close()
    if not bot:
        return {"error": f"bot {bot_id} not found"}
    try:
        bars = _bars_for_bot(bot)
    except BrokerError as exc:
        return {"error": f"Binance error: {exc}"}
    if len(bars) < 60:
        return {"error": f"only {len(bars)} bars available"}
    try:
        strategy = build(bot["kind"], **(bot.get("params") or {}))
    except (TypeError, ValueError) as exc:
        return {"error": f"bad strategy parameters: {exc}"}
    config = make_config(bars, **(bot.get("config") or {}))
    result = run_backtest(bars, strategy, config)

    curve = result.equity_curve
    step = max(1, len(curve) // MAX_CURVE_POINTS)
    equity_points = [
        [idx.isoformat(), float(curve.iloc[k])]
        for k, idx in enumerate(curve.index) if k % step == 0
    ]
    close = result.close
    bh_step = max(1, len(close) // MAX_CURVE_POINTS)
    bh_points = [
        [idx.isoformat(), float(close.iloc[k])]
        for k, idx in enumerate(close.index) if k % bh_step == 0
    ]
    trades_payload = [
        {
            "entry_ts": t.entry_ts.isoformat(), "exit_ts": t.exit_ts.isoformat(),
            "qty": t.qty, "entry_price": t.entry_price, "exit_price": t.exit_price,
            "return_pct": t.return_pct, "reason_open": t.reason_open, "reason_close": t.reason_close,
        }
        for t in result.trades[-MAX_TRADES_SHOWN:]
    ]
    conn = _db_conn()
    try:
        run_id = store.save_run(
            conn,
            {
                "bot_id": bot_id, "kind": "backtest", "symbol": bot["symbol"],
                "timeframe": bot["timeframe"], "strategy": bot["kind"],
                "params": bot.get("params") or {}, "metrics": result.metrics,
                "equity": equity_points, "buy_and_hold": bh_points,
                "trades": trades_payload, "bars": len(bars),
                "ohlc": _ohlc_payload(bars),
            },
        )
        run = store.get_run(conn, run_id)
    finally:
        conn.close()
    return _jsonify(
        {
            "run": run,
            "equity_curve": equity_points, "buy_and_hold_curve": bh_points,
            "ohlc": _ohlc_payload(bars),
            "trades": trades_payload, "halted_reason": result.halted_reason,
            "initial_equity": result.initial_equity, "final_equity": result.final_equity,
        }
    )


def api_runs_list(bot_id: int | None = None) -> dict[str, Any]:
    conn = _db_conn()
    try:
        return _jsonify({"runs": store.list_runs(conn, bot_id=bot_id)})
    finally:
        conn.close()


def api_run_get(run_id: int) -> dict[str, Any]:
    conn = _db_conn()
    try:
        run = store.get_run(conn, run_id)
    finally:
        conn.close()
    if not run:
        return {"error": f"run {run_id} not found"}
    return _jsonify(
        {
            "run": run,
            "equity_curve": _json_loads(run.get("equity")),
            "buy_and_hold_curve": _json_loads(run.get("buy_and_hold")),
            "trades": _json_loads(run.get("trades")),
        }
    )


def _json_loads(raw: Any) -> Any:
    if raw is None or isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []


def api_paper_trades() -> dict[str, Any]:
    """Sync demo-account fills into paper_trades and compute running P&L."""
    try:
        client = BinanceLiveClient()
    except BrokerError as exc:
        return {"error": f"Binance error: {exc}"}
    conn = _db_conn()
    try:
        symbols = [b["symbol"] for b in store.list_bots(conn)] or [
            "BTC/USDT", "ETH/USDT", "SOL/USDT",
        ]
        try:
            fills = client.closed_fills(symbols)
        except BrokerError as exc:
            return {"error": f"Binance error: {exc}"}
        store.upsert_paper_trades(conn, fills)
        rows = store.list_paper_trades(conn)
    finally:
        conn.close()

    # Average-cost realized P&L per symbol, in chronological order.
    by_symbol: dict[str, dict[str, float]] = {}
    for r in sorted(rows, key=lambda x: x["filled_at"] or ""):
        state = by_symbol.setdefault(r["symbol"], {"qty": 0.0, "cost": 0.0, "realized": 0.0})
        signed = r["qty"] if r["side"] == "buy" else -r["qty"]
        if signed > 0:
            state["cost"] += signed * r["price"]
            state["qty"] += signed
        else:
            closed = min(-signed, state["qty"])
            if state["qty"] > 0:
                avg = state["cost"] / state["qty"]
                state["realized"] += (r["price"] - avg) * closed
                state["cost"] -= avg * closed
                state["qty"] -= closed
    summary = [
        {"symbol": s, "realized_pnl": v["realized"], "open_qty": v["qty"]}
        for s, v in sorted(by_symbol.items(), key=lambda kv: kv[1]["realized"])
    ]
    return _jsonify({"trades": list(reversed(rows)), "summary": summary})


def api_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = (payload.get("symbol") or "").strip()
    timeframes = [t.strip() for t in (payload.get("timeframes") or ["1d"]) if t.strip()]
    days = int(payload.get("days") or 365)
    if not symbol:
        return {"error": "symbol required"}
    src, fetch = _resolve_feed(symbol, payload.get("source", "binance"))
    conn = _db_conn()
    ingested = []
    try:
        for tf in timeframes:
            bars = load_scan_bars(fetch, tf, source=src, days=days, n_bars=10_000,
                                  cache_key=symbol)
            n = store.upsert_bars(conn, symbol, tf, bars)
            ingested.append(
                {"timeframe": tf, "bars": n, "source": src,
                 "first": bars[0].ts.isoformat(), "last": bars[-1].ts.isoformat()}
            )
        counts = store.bar_counts(conn)
    finally:
        conn.close()
    return _jsonify({"ingested": ingested, "counts": counts})


def api_bars_status() -> dict[str, Any]:
    conn = _db_conn()
    try:
        return _jsonify({"counts": store.bar_counts(conn)})
    finally:
        conn.close()


def api_presets_list() -> dict[str, Any]:
    conn = _db_conn()
    try:
        return _jsonify({"presets": store.list_presets(conn)})
    finally:
        conn.close()


def api_presets_create(payload: dict[str, Any]) -> dict[str, Any]:
    if _read_only():
        return {"error": "prod is read-only: presets are created on paper"}
    name = (payload.get("name") or "").strip()
    kind = (payload.get("kind") or "").strip()
    if not name or kind not in REGISTRY:
        return {"error": "name and a valid strategy kind are required"}
    params = payload.get("params") or {}
    try:
        build(kind, **params)
    except (TypeError, ValueError) as exc:
        return {"error": f"bad strategy parameters: {exc}"}
    conn = _db_conn()
    try:
        preset = store.save_preset(conn, name, kind, params, payload.get("notes", ""))
        return _jsonify({"preset": preset})
    finally:
        conn.close()


def api_presets_delete(preset_id: int) -> dict[str, Any]:
    if _read_only():
        return {"error": "prod is read-only: presets are created on paper"}
    conn = _db_conn()
    try:
        return {"deleted": store.delete_preset(conn, preset_id)}
    finally:
        conn.close()


class WebHandler(BaseHTTPRequestHandler):
    """Routes; all heavy lifting is in the ``api_*`` functions (unit-testable)."""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/" or self.path.startswith("/index.html"):
            html = render_index()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/api/env":
            self._send_json(200, env_describe())
        elif self.path == "/api/strategies":
            self._send_json(200, api_strategies())
        elif self.path == "/api/account":
            self._send_json(200, api_account())
        elif self.path == "/api/symbols":
            self._send_json(200, api_symbols())
        elif self.path == "/api/bots":
            self._send_json(200, api_bots_list())
        elif self.path == "/api/runs":
            self._send_json(200, api_runs_list())
        elif self.path == "/api/paper/trades":
            self._send_json(200, api_paper_trades())
        elif self.path == "/api/bars":
            self._send_json(200, api_bars_status())
        elif self.path == "/api/presets":
            self._send_json(200, api_presets_list())
        elif self.path.startswith("/api/bots/") and self.path.endswith("/run"):
            bot_id = int(self.path.split("/")[3])
            self._send_json(200, api_bot_run(bot_id))
        elif self.path.startswith("/api/runs/"):
            self._send_json(200, api_run_get(int(self.path.rsplit("/", 1)[1])))
        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        parts = self.path.strip("/").split("/")
        if self.path.startswith("/api/bots/") and len(parts) == 3:
            self._send_json(200, api_bots_delete(int(parts[2])))
        elif self.path.startswith("/api/presets/") and len(parts) == 3:
            self._send_json(200, api_presets_delete(int(parts[2])))
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        body = self._read_body()
        if self.path == "/api/backtest":
            self._send_json(200, api_backtest(body))
        elif self.path == "/api/scan":
            self._send_json(200, api_scan(body))
        elif self.path == "/api/bots":
            self._send_json(200, api_bots_create(body))
        elif self.path == "/api/presets":
            self._send_json(200, api_presets_create(body))
        elif self.path == "/api/ingest":
            self._send_json(200, api_ingest(body))
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class WebApp:
    def __init__(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.host = host
        self.port = port

    def start(self) -> ThreadingHTTPServer:
        return ThreadingHTTPServer((self.host, self.port), WebHandler)

    def serve_forever(self) -> None:
        httpd = self.start()
        url = f"http://{self.host}:{httpd.server_port}/"
        print(f"[web] dashboard running at {url}  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
