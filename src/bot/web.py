"""Local web dashboard for the bot: backtests, timeframe scans, paper account.

    python -m bot web --port 8000

Serves a single-page UI (``web/index.html``) plus a small JSON API.  Runs on
the user's machine so the Alpaca endpoints are reachable.  Same honesty rules
as the rest of the project: the only order-related endpoint belongs to the
paper trader, and nothing here can reach a live broker.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .alpaca_client import AlpacaClient, AlpacaError, PAPER_TRADING_BASE
from .backtest import run_backtest
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import generate_synthetic
from .metrics import infer_bars_per_year
from .scanlib import load_scan_bars, make_config, run_scan
from .strategy import REGISTRY, build
from . import db as store

WEB_DIR = Path(__file__).resolve().parent / "web"

# Curated ticker lists for the terminal dropdowns.  Each entry carries the
# feeds that can serve it: Alpaca trades a subset of crypto pairs; everything
# else backtests through TradingView data (tvDatafeed, unofficial).
SYMBOLS = {
    "stocks": [
        {"symbol": "SPY", "alpaca": True},
        {"symbol": "QQQ", "alpaca": True},
        {"symbol": "AAPL", "alpaca": True},
        {"symbol": "MSFT", "alpaca": True},
        {"symbol": "NVDA", "alpaca": True},
        {"symbol": "TSLA", "alpaca": True},
        {"symbol": "AMZN", "alpaca": True},
        {"symbol": "META", "alpaca": True},
        {"symbol": "GOOGL", "alpaca": True},
        {"symbol": "AMD", "alpaca": True},
        {"symbol": "NFLX", "alpaca": True},
        {"symbol": "JPM", "alpaca": True},
        {"symbol": "V", "alpaca": True},
        {"symbol": "UNH", "alpaca": True},
        {"symbol": "XOM", "alpaca": True},
        {"symbol": "KO", "alpaca": True},
    ],
    "crypto": [
        # The user's full watchlist.  alpaca=False pairs backtest via TV data
        # and cannot paper-trade through Alpaca (not listed there).
        {"symbol": "BTC/USD", "alpaca": True, "tv": "BINANCE:BTCUSDT"},
        {"symbol": "ETH/USD", "alpaca": True, "tv": "BINANCE:ETHUSDT"},
        {"symbol": "SOL/USD", "alpaca": True, "tv": "BINANCE:SOLUSDT"},
        {"symbol": "XRP/USD", "alpaca": True, "tv": "BINANCE:XRPUSDT"},
        {"symbol": "LINK/USD", "alpaca": True, "tv": "BINANCE:LINKUSDT"},
        {"symbol": "DOGE/USD", "alpaca": True, "tv": "BINANCE:DOGEUSDT"},
        {"symbol": "AVAX/USD", "alpaca": True, "tv": "BINANCE:AVAXUSDT"},
        {"symbol": "LTC/USD", "alpaca": True, "tv": "BINANCE:LTCUSDT"},
        {"symbol": "BCH/USD", "alpaca": True, "tv": "BINANCE:BCHUSDT"},
        {"symbol": "DOT/USD", "alpaca": False, "tv": "BINANCE:DOTUSDT"},
        {"symbol": "BNB/USDT", "alpaca": False, "tv": "BINANCE:BNBUSDT"},
        {"symbol": "HYPE/USDT", "alpaca": False, "tv": "HYPE:HYPEUSDT"},
        {"symbol": "UNI/USD", "alpaca": False, "tv": "BINANCE:UNIUSDT"},
        {"symbol": "JUP/USDT", "alpaca": False, "tv": "BINANCE:JUPUSDT"},
        {"symbol": "PYTH/USDT", "alpaca": False, "tv": "BINANCE:PYTHUSDT"},
        {"symbol": "CAKE/USDT", "alpaca": False, "tv": "BINANCE:CAKEUSDT"},
        {"symbol": "CC/USDC", "alpaca": False, "tv": "MEXC:CCUSDC"},
    ],
    "commodities": [
        {"symbol": "XAUT/USD", "alpaca": False, "tv": "BITFINEX:XAUTUSD", "label": "Tether Gold"},
        {"symbol": "XAG/USD", "alpaca": False, "tv": "OANDA:XAGUSD", "label": "Silver spot"},
        {"symbol": "USOIL", "alpaca": False, "tv": "TVC:USOIL", "label": "WTI Crude Oil"},
    ],
    "indices": [
        {"symbol": "BTC.D", "alpaca": False, "tv": "CRYPTOCAP:BTC.D", "label": "Bitcoin dominance"},
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
    source = payload.get("source", "alpaca")
    symbol = payload.get("symbol", "BTC/USD")
    interval = payload.get("interval", "1d")
    days = int(payload.get("days") or 365)
    src, fetch = _resolve_feed(symbol, source)
    return load_scan_bars(fetch, interval, source=src, days=days)


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
    except AlpacaError as exc:
        return {"error": f"Alpaca error: {exc}"}
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
    symbols = [s.strip() for s in (payload.get("symbols") or ["BTC/USD"]) if s.strip()]
    timeframes = [t.strip() for t in (payload.get("timeframes") or ["1d"]) if t.strip()]
    strategies = [s.strip() for s in (payload.get("strategies") or ["sma_cross"]) if s.strip()]
    source = payload.get("source", "alpaca")
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
    except AlpacaError as exc:
        return {"error": f"Alpaca error: {exc}"}
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
    """Scan across symbols, resolving each to its feed (Alpaca or TV)."""
    feeds = {s: _resolve_feed(s, source) for s in symbols}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        src, fetch = feeds[symbol]
        for tf in timeframes:
            try:
                bars = load_scan_bars(fetch, tf, source=src, days=days, n_bars=10_000)
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
        client = AlpacaClient()
    except AlpacaError as exc:
        return {"error": str(exc)}
    try:
        acct = client.get_account()
        positions = client.get_positions()
    except AlpacaError as exc:
        return {"error": f"Alpaca error: {exc}"}
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
    conn = _db_conn()
    try:
        return {"deleted": store.delete_bot(conn, bot_id)}
    finally:
        conn.close()


def _bars_for_bot(bot: dict[str, Any], days: int = 730) -> list[Any]:
    """Bars for a bot: Postgres cache when rich enough, else Alpaca."""
    symbol, tf = bot["symbol"], bot["timeframe"]
    start = datetime.now(timezone.utc) - timedelta(days=days)
    conn = None
    if store.is_available():
        try:
            conn = store.connect()
            cached = store.load_bars(conn, symbol, tf, start=start)
            if len(cached) >= 200:
                return cached
        except store.DatabaseError:
            conn = None
    client = AlpacaClient()
    if tf == "6h":
        from .data.feed import aggregate_bars

        raw = client.fetch_bars(symbol, "1h", start=start, limit=10_000)
        bars = aggregate_bars(raw, 6)
    else:
        bars = client.fetch_bars(symbol, tf, start=start, limit=10_000)
    if conn is not None:
        try:
            store.upsert_bars(conn, symbol, tf, bars)
        except store.DatabaseError:
            pass
    return bars


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
    except AlpacaError as exc:
        return {"error": f"Alpaca error: {exc}"}
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
            },
        )
        run = store.get_run(conn, run_id)
    finally:
        conn.close()
    return _jsonify(
        {
            "run": run,
            "equity_curve": equity_points, "buy_and_hold_curve": bh_points,
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
    """Sync closed paper orders from Alpaca and compute running P&L."""
    try:
        client = AlpacaClient()
        orders = client._request(
            f"{PAPER_TRADING_BASE}/v2/orders?status=closed&limit=500"
        )
    except AlpacaError as exc:
        return {"error": f"Alpaca error: {exc}"}
    if not isinstance(orders, list):
        return {"error": "unexpected Alpaca response"}
    fills = []
    for o in orders:
        if not o.get("filled_at") or not o.get("filled_avg_price"):
            continue
        fills.append(
            {
                "order_id": o["id"],
                "symbol": o["symbol"],
                "side": o.get("side", ""),
                "qty": float(o.get("filled_qty") or 0),
                "price": float(o.get("filled_avg_price") or 0),
                "filled_at": o["filled_at"],
                "bot_name": (o.get("client_order_id") or "").split(":")[0][:60],
            }
        )
    fills.sort(key=lambda f: f["filled_at"])
    conn = _db_conn()
    try:
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
    src, fetch = _resolve_feed(symbol, payload.get("source", "alpaca"))
    conn = _db_conn()
    ingested = []
    try:
        for tf in timeframes:
            bars = load_scan_bars(fetch, tf, source=src, days=days, n_bars=10_000)
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
            html = (WEB_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
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
