"""Local web dashboard for the bot: backtests, timeframe scans, demo account.

    python -m bot web --port 8000

Serves a single-page UI (``web/index.html``) plus a small JSON API.  Bars
come from Binance (production klines, Postgres-cached); the account tab reads
the demo wallet.  Same honesty rules as the rest of the project: the only
order-related endpoint belongs to the demo trader, and nothing here can
reach a live broker.
"""

from __future__ import annotations

import inspect
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .exchange.binance import BrokerError
from .exchange.order_client import BinanceOrderClient
from .backtest import run_backtest
from . import promotions as promo
from .config import BotConfig, CostConfig, RiskConfig
from .envcfg import describe as env_describe, env_name, lab_mode, state_dir as env_state_dir
from .data.feed import generate_synthetic
from .metrics import infer_bars_per_year
from .scanlib import load_scan_bars, make_config, run_scan
from .strategy import REGISTRY, build
from . import db as store

WEB_DIR = Path(__file__).resolve().parent / "web"

# The live dashboard is read-only: a strategy reaches live by promotion
# package, never by clicking "create bot" against the live database.
READ_ONLY_ENVS = ("live",)


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
    if env == "live":
        label = "LIVE"
        note = "REAL MONEY · venue not configured"
        foot = "LIVE<br>real money · no venue yet"
    else:
        label = "DEMO"
        note = "simulated fills · no real money"
        foot = "DEMO<br>no real orders possible"
    tokens = {
        "{{ENV}}": env,
        "{{ENV_NAME}}": f"{env} · terminal",
        "{{ENV_LABEL}}": label,
        "{{ENV_NOTE}}": note,
        "{{ENV_FOOT}}": foot,
        "{{ENV_JSON}}": json.dumps(env),
        "{{LAB_JSON}}": json.dumps(lab_mode()),
    }
    for token, value in tokens.items():
        html = html.replace(token, value)
    return html.encode("utf-8")


# The mark is an equity line rising into a live dot, on a dark tile: legible
# at 16 px, and the world sets the colour so two open tabs never look alike.
ICON_ACCENT = {"demo": "#0ecb81", "live": "#f6465d"}
ICON_FALLBACK = "#4f8cff"


def favicon_svg() -> bytes:
    """The tab icon, coloured for this world (green demo, red live)."""
    try:
        accent = ICON_ACCENT.get(env_name(), ICON_FALLBACK)
    except ValueError:
        accent = ICON_FALLBACK
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="trading-bot">
<rect x="1" y="1" width="62" height="62" rx="15" fill="{accent}"/>
<path d="M12 48h40" stroke="#0b0e11" stroke-opacity=".55" stroke-width="3" stroke-linecap="round"/>
<path d="M13 43L27 31l7 7 15-15" fill="none" stroke="#0b0e11" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="49" cy="23" r="5" fill="#0b0e11"/>
</svg>
""".encode("utf-8")

# Curated ticker lists for the terminal dropdowns.  Crypto pairs come from
# Binance; the other lists exist only as TradingView research feeds
# (tvDatafeed, unofficial) and cannot be demo-traded.
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
        {"symbol": "HYPE/USDT", "tv": "BINANCE:HYPEUSDT"},
        {"symbol": "UNI/USDT", "tv": "BINANCE:UNIUSDT"},
        {"symbol": "JUP/USDT", "tv": "BINANCE:JUPUSDT"},
        {"symbol": "PYTH/USDT", "tv": "BINANCE:PYTHUSDT"},
        {"symbol": "CAKE/USDT", "tv": "BINANCE:CAKEUSDT"},
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
# The dry-run unit polls every 15 minutes; allow one missed cycle before a
# sleeve is called stale, so a normal wait never looks like a dead process.
DRYRUN_FRESH_SECONDS = 1200


def _json_default(value: Any) -> Any:
    """Encode what json.dumps cannot on its own."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _jsonify(value: Any) -> Any:
    """Recursively convert NaN/None to null and datetimes to ISO strings."""
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, Decimal):
        # Postgres NUMERIC (a bot's capital, an equity) arrives as Decimal and
        # json.dumps refuses it - that alone 500s /api/bots (2026-11-28).
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


def api_strategies(base: Any = None) -> dict[str, Any]:
    """The strategies this world may run.

    Lab sees the whole registry, because that is where ideas are tested.
    Demo and live see only what a promotion manifest activates: code that
    merely exists is not a strategy available in that world. The dashboard
    therefore lists exactly what could trade.
    """
    manifests = None if lab_mode() else promo.active_manifests(env_name(), base)
    out: dict[str, Any] = {}
    for name, cls in sorted(REGISTRY.items()):
        if manifests is not None and name not in manifests:
            continue
        sig = inspect.signature(cls.__init__)
        defaults = {
            p: (None if (v := sig.parameters[p].default) is inspect.Parameter.empty else v)
            for p in cls.PARAMS if p in sig.parameters
        }
        entry: dict[str, Any] = {
            "params": list(cls.PARAMS),
            "defaults": defaults,
            "doc": (cls.__doc__ or "").strip().splitlines()[0],
        }
        if manifests is not None:
            manifest = manifests[name]
            entry.update({
                "symbols": list(manifest.symbols),
                "timeframe": manifest.timeframe,
                "capital": manifest.capital,
                "promoted_at": manifest.promoted_at,
                "override": manifest.override,
            })
        out[name] = entry
    return out


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
    """Wallet value per asset, priced in USDT, plus open positions.

    The payload says which venue produced it. live never shows demo
    balances: the client refuses before this point, and the error carries
    ``venue: live`` so the UI can say *not configured* instead of *offline*.
    """
    env = env_name()
    venue = "live" if env == "live" else "demo"
    try:
        client = BinanceOrderClient()
        acct = client.get_account()
        positions = client.get_positions()
        balances = client.broker.balances()
    except BrokerError as exc:
        return {
            "error": str(exc),
            "env": env,
            "venue": venue,
            "status": "NOT_CONFIGURED" if venue == "live" else "ERROR",
            "assets": [],
            "positions": [],
        }
    equity = float(acct.get("equity") or 0)
    assets: list[dict[str, Any]] = []
    for asset, bal in balances.items():
        try:
            price: float | None = float(client._price(asset))
        except Exception:  # noqa: BLE001 - unpriced dust is still shown, just without a value
            price = None
        value = bal.total * price if price is not None else None
        assets.append({
            "asset": asset,
            "free": bal.free,
            "locked": bal.locked,
            "total": bal.total,
            "price": price,
            "value": value,
            "share": (value / equity) if (value is not None and equity > 0) else None,
        })
    assets.sort(key=lambda a: a["value"] or 0.0, reverse=True)
    return _jsonify(
        {
            "account_number": acct.get("account_number"),
            "status": acct.get("status"),
            "env": env,
            "venue": venue,
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "equity": equity,
            "cash": float(acct.get("cash") or 0),
            "buying_power": float(acct.get("buying_power") or 0),
            "unrealized_pl": sum(float(p.get("unrealized_pl") or 0) for p in positions),
            "assets": assets,
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


def _run_rate(conn: Any, bot_id: int, capital: float) -> dict[str, Any] | None:
    """Average P&L per day over the last 7 days of equity snapshots.

    Deliberately not annualised from two samples: a bot that is hours old has
    no rate, and the label says the window it measured.
    """
    rows = conn.execute(
        "SELECT ts, equity FROM equity_snapshots "
        "WHERE bot_id = %s AND ts >= now() - interval '7 days' ORDER BY ts",
        (bot_id,),
    ).fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    days = (last["ts"] - first["ts"]).total_seconds() / 86400.0
    if days < 0.05:  # under ~70 minutes: too short to call a rate
        return None
    per_day = (float(last["equity"]) - float(first["equity"])) / days
    return {
        "window_days": round(days, 2),
        "per_day": per_day,
        "month_pct": (per_day * 30.0 / capital * 100.0) if capital > 0 else None,
        "samples": len(rows),
    }


def api_dryrun(base: Path | None = None, state_dir: Path | None = None) -> dict[str, Any]:
    """Status of the demo dry-run sleeve replays.

    Dry-run sleeves are not rows in bots: one process replays every active
    manifest and writes <state>/<strategy>/dryrun/status.json. The Bots page
    therefore cannot show them from the trading-bot table; this endpoint is
    the read-only view the dashboard uses so they are not invisible.
    """
    if env_name() != "demo":
        return {"sleeves": [], "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    manifests = promo.active_manifests("demo", base)
    root = Path(state_dir) if state_dir is not None else env_state_dir("demo")
    now = datetime.now(timezone.utc)
    sleeves: list[dict[str, Any]] = []
    for name, manifest in sorted(manifests.items()):
        for symbol in manifest.symbols:
            path = root / name / "dryrun" / "status.json"
            row: dict[str, Any] = {
                "strategy": name,
                "symbol": symbol,
                "timeframe": manifest.timeframe,
                "capital": manifest.capital,
                "promoted_at": manifest.promoted_at,
                "state": "waiting",
                "updated_at": None,
                "age_s": None,
            }
            runner: Any = None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                runner = (data.get("runners") or {}).get(symbol)
            except (OSError, ValueError):
                runner = None
            if isinstance(runner, dict):
                updated = runner.get("updated_at")
                age = None
                if updated:
                    try:
                        stamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                        age = (now - stamp).total_seconds()
                    except (TypeError, ValueError):
                        age = None
                fresh = age is not None and age <= DRYRUN_FRESH_SECONDS
                row.update({
                    "state": "running" if fresh else "stale",
                    "updated_at": updated,
                    "age_s": int(age) if age is not None else None,
                    "history": runner.get("history"),
                    "dryrun": runner.get("dryrun"),
                    "position": runner.get("position"),
                    "last_bar": runner.get("last_bar"),
                    "window": runner.get("window"),
                    "deploy": runner.get("deploy"),
                    "halted_reason": runner.get("halted_reason") or "",
                })
            sleeves.append(row)
    # Newest promotion first: the sleeves just shipped are the ones an owner
    # opens the dashboard to check, not the oldest replay in the folder.
    sleeves.sort(key=lambda item: str(item.get("promoted_at") or ""), reverse=True)
    return _jsonify({
        "sleeves": sleeves,
        "as_of": now.isoformat(timespec="seconds"),
        "state_dir": str(root),
    })


def _equity_contributors(conn: Any, days: int) -> list[tuple[str, list[tuple[datetime, float]]]]:
    """Per-wallet equity histories for this world.

    Two kinds of contributor: trading bots (equity_snapshots rows, one series
    per bot) and, in demo, dry-run sleeves (the <state>/<strategy>/dryrun/
    <SYMBOL>.equity.csv files the dry-run runner appends to). One merged
    portfolio curve is built from them in api_equity; keeping the contributors
    separate here is what makes the merge testable without a database.
    """
    out: list[tuple[str, list[tuple[datetime, float]]]] = []
    rows = conn.execute(
        "SELECT b.name, e.ts, e.equity FROM equity_snapshots e "
        "JOIN bots b ON b.id = e.bot_id "
        "WHERE e.ts >= now() - (%s || ' days')::interval ORDER BY e.ts",
        (str(days),),
    ).fetchall()
    per_bot: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        per_bot.setdefault(str(row["name"]), []).append(
            (row["ts"], float(row["equity"]))
        )
    for name, series in per_bot.items():
        out.append((name, series))
    if env_name() == "demo":
        root = env_state_dir("demo")
        try:
            manifests = promo.active_manifests("demo")
        except Exception:  # noqa: BLE001 - a broken promotions dir must not 500 the widget
            manifests = {}
        for name, manifest in sorted(manifests.items()):
            for symbol in manifest.symbols:
                path = root / name / "dryrun" / f"{symbol.replace('/', '')}.equity.csv"
                try:
                    lines = path.read_text(encoding="utf-8").strip().splitlines()
                except OSError:
                    continue
                series: list[tuple[datetime, float]] = []
                for line in lines[1:]:  # header: ts, equity, deploy_return, position_qty
                    parts = line.split(",")
                    if len(parts) < 2:
                        continue
                    try:
                        ts = datetime.fromisoformat(parts[0].strip().replace("Z", "+00:00"))
                        series.append((ts, float(parts[1])))
                    except (ValueError, TypeError):
                        continue
                if len(series) >= 2:
                    out.append((f"{name}:{symbol}", series))
    return out


def api_equity(max_points: int = 400, days: int = 730) -> dict[str, Any]:
    """Portfolio equity curve for this world, for the dashboard widget.

    At each timestamp the portfolio is the sum of every contributor's latest
    known equity, so a sleeve or bot that starts later joins the curve at its
    own capital instead of being read as a loss. Downsampled to at most
    ``max_points`` buckets, last value of each bucket wins: the final point is
    always the true current equity, and a flat tail is honest (a 4h system
    with one poll a day genuinely has one point a day).
    """
    now = datetime.now(timezone.utc)
    contributors: list[tuple[str, list[tuple[datetime, float]]]]
    try:
        conn = _db_conn()
    except ValueError:
        conn = None
    try:
        contributors = _equity_contributors(conn, days) if conn is not None else []
    finally:
        if conn is not None:
            conn.close()
    # Merge: one pointer per contributor over its own sorted series.
    stamps = sorted({ts for _, series in contributors for ts, _ in series})
    cursors = [0] * len(contributors)
    merged: list[tuple[datetime, float]] = []
    for ts in stamps:
        total = 0.0
        for i, (_, series) in enumerate(contributors):
            while cursors[i] + 1 < len(series) and series[cursors[i] + 1][0] <= ts:
                cursors[i] += 1
            if series[cursors[i]][0] <= ts:
                total += series[cursors[i]][1]
        merged.append((ts, total))
    # Bucket to max_points, newest value of each bucket wins.
    if len(merged) > max_points:
        bucket = len(merged) / max_points
        merged = [merged[min(len(merged) - 1, int((i + 1) * bucket) - 1)]
                  for i in range(max_points)]
    return _jsonify({
        "series": [{"ts": ts.isoformat(timespec="seconds"), "equity": eq}
                   for ts, eq in merged],
        "contributors": len(contributors),
        "as_of": now.isoformat(timespec="seconds"),
    })


def api_running() -> dict[str, Any]:
    """Bots the database believes exist, with liveness and run rate.

    ``alive`` requires a heartbeat inside the last five minutes: a row whose
    process died still says ``running`` in the database, and the dashboard
    must not repeat that lie.
    """
    conn = _db_conn()
    try:
        now = datetime.now(timezone.utc)
        bots = [_decorate_bot(conn, b, now) for b in store.bot_lifecycle(conn, limit=50)]
        return _jsonify({"bots": bots, "as_of": now.isoformat(timespec="seconds")})
    finally:
        conn.close()


def _decorate_bot(conn: Any, b: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Liveness, return and run rate for one lifecycle row."""
    b["capital"] = float(b["capital"] or 0.0)
    b["equity"] = float(b["equity"]) if b.get("equity") is not None else None
    b["realized"] = float(b["realized"] or 0.0)
    b["halted"] = bool(b.get("halted"))
    b["state"] = b.get("state") or {}
    b["config"] = b.get("config") or {}
    age = None
    if b.get("last_heartbeat_at"):
        age = (now - b["last_heartbeat_at"]).total_seconds()
        b["heartbeat_age_s"] = int(age)
    b["alive"] = bool(age is not None and age < 300 and b.get("status") == "running")
    # How long this run has been up. Blank unless the loop is actually alive:
    # a dead row's started_at would count the time it has been dead too.
    b["uptime_s"] = (
        int((now - b["started_at"]).total_seconds())
        if (b["alive"] and b.get("started_at")) else None
    )
    b["return_pct"] = (
        (b["equity"] / b["capital"] - 1.0) * 100.0
        if (b["equity"] is not None and b["capital"] > 0) else None
    )
    b["run_rate"] = _run_rate(conn, int(b["id"]), b["capital"])
    return b


def _venue_orders(symbol: str) -> tuple[list[dict[str, Any]], str | None]:
    """Resting orders on the venue, or why they cannot be read."""
    try:
        from .exchange.order_client import BinanceOrderClient

        return BinanceOrderClient().open_orders(symbol), None
    except Exception as exc:  # noqa: BLE001 - the page explains, never raises
        return [], str(exc)


def api_bot_detail(bot_id: int) -> dict[str, Any]:
    """Everything the dashboard shows for one bot, in one call."""
    conn = _db_conn()
    try:
        rows = store.bot_lifecycle(conn, limit=1, bot_id=int(bot_id))
        now = datetime.now(timezone.utc)
        bot = _decorate_bot(conn, rows[0], now) if rows else None
        trades = store.list_bot_trades(conn, int(bot_id), limit=50) if bot else []
    finally:
        conn.close()
    if bot is None:
        return {"error": f"no bot {bot_id} in this world"}
    orders: list[dict[str, Any]] = []
    orders_error: str | None = None
    # live has no venue, so this must degrade, not throw: the levels the loop
    # holds locally are still the answer to "where is my stop".
    orders, orders_error = _venue_orders(bot["symbol"])
    return _jsonify({
        "bot": bot, "trades": trades, "open_orders": orders,
        "orders_error": orders_error, "as_of": now.isoformat(timespec="seconds"),
    })


def api_bot_halt(bot_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Kill switch.  Deliberately allowed in live: stopping trading must
    never wait for a promotion."""
    if payload.get("halted") is None:
        return {"error": "halted (true or false) is required"}
    halted = bool(payload.get("halted"))
    reason = str(payload.get("reason") or "")[:200]
    conn = _db_conn()
    try:
        if not store.set_bot_halted(conn, int(bot_id), halted, reason):
            return {"error": f"no bot {bot_id} in this world"}
        store.log_event(
            conn, int(bot_id), "info",
            f"kill switch {'engaged' if halted else 'released'}",
            payload={"halted": halted, "reason": reason, "actor": "dashboard"},
        )
        conn.commit()
        return {"bot_id": int(bot_id), "halted": halted, "reason": reason}
    finally:
        conn.close()

def api_bots_create(payload: dict[str, Any]) -> dict[str, Any]:
    if _read_only():
        return {"error": "live is read-only: create bots on demo, then promote"}
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
    if not lab_mode():
        try:
            manifest = promo.require(kind, env_name())
        except promo.PromotionError as exc:
            return {"error": str(exc)}
        if symbol not in manifest.symbols:
            covered = ", ".join(manifest.symbols)
            return {"error": f"{kind} is promoted on {covered} only; {symbol} is not covered"}
        if timeframe != manifest.timeframe:
            return {"error": f"{kind} is promoted on {manifest.timeframe}; this bot asks for {timeframe}"}
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
            kind=kind, params=params, config=config, mode=payload.get("mode", "demo"),
        )
        return _jsonify({"bot": bot})
    finally:
        conn.close()


def api_bots_delete(bot_id: int) -> dict[str, Any]:
    if _read_only():
        return {"error": "live is read-only: demote on demo, then promote"}
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


def api_demo_trades() -> dict[str, Any]:
    """Sync demo-account fills into demo_trades and compute running P&L."""
    try:
        client = BinanceOrderClient()
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
        store.upsert_demo_trades(conn, fills)
        rows = store.list_demo_trades(conn)
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
        return {"error": "live is read-only: presets are created on demo"}
    name = (payload.get("name") or "").strip()
    kind = (payload.get("kind") or "").strip()
    if not name or kind not in REGISTRY:
        return {"error": "name and a valid strategy kind are required"}
    params = payload.get("params") or {}
    try:
        build(kind, **params)
    except (TypeError, ValueError) as exc:
        return {"error": f"bad strategy parameters: {exc}"}
    if not lab_mode():
        try:
            promo.require(kind, env_name())
        except promo.PromotionError as exc:
            return {"error": str(exc)}
    conn = _db_conn()
    try:
        preset = store.save_preset(conn, name, kind, params, payload.get("notes", ""))
        return _jsonify({"preset": preset})
    finally:
        conn.close()


def api_presets_delete(preset_id: int) -> dict[str, Any]:
    if _read_only():
        return {"error": "live is read-only: presets are created on demo"}
    conn = _db_conn()
    try:
        return {"deleted": store.delete_preset(conn, preset_id)}
    finally:
        conn.close()


class WebHandler(BaseHTTPRequestHandler):
    """Routes; all heavy lifting is in the ``api_*`` functions (unit-testable)."""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        # Last line of defence: a payload built anywhere else may still hold a
        # Decimal, and one unencodable field must not take the whole page down.
        data = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
            # The page is rendered per world and changes with every deploy.
            # A cached copy shows another world's banner or a retired view.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path.split("?")[0] == "/favicon.svg":
            data = favicon_svg()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            # Short cache: the world's colour is baked in, and a deploy may
            # restyle it.  A day is long enough that the tab is instant.
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.split("?")[0] == "/favicon.ico":
            # Browsers still ask for the legacy path; answer with the real one.
            self.send_response(302)
            self.send_header("Location", "/favicon.svg")
            self.send_header("Content-Length", "0")
            self.end_headers()
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
        elif self.path == "/api/dryrun":
            self._send_json(200, api_dryrun())
        elif self.path == "/api/equity":
            self._send_json(200, api_equity())
        elif self.path == "/api/running":
            self._send_json(200, api_running())
        elif self.path == "/api/runs":
            self._send_json(200, api_runs_list())
        elif self.path == "/api/demo/trades":
            self._send_json(200, api_demo_trades())
        elif self.path == "/api/bars":
            self._send_json(200, api_bars_status())
        elif self.path == "/api/presets":
            self._send_json(200, api_presets_list())
        elif self.path.startswith("/api/bots/") and self.path.endswith("/run"):
            bot_id = int(self.path.split("/")[3])
            self._send_json(200, api_bot_run(bot_id))
        elif self.path.startswith("/api/bots/"):
            try:
                bot_id = int(self.path.rsplit("/", 1)[1])
            except ValueError:
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, api_bot_detail(bot_id))
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
        elif self.path.startswith("/api/bots/") and self.path.endswith("/halt"):
            try:
                bot_id = int(self.path.split("/")[3])
            except ValueError:
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, api_bot_halt(bot_id, body))
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
