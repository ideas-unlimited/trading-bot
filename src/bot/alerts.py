"""TradingView alert receiver: a webhook server that *mirrors* strategy signals.

TradingView's paper trading fills orders inside TradingView.  This module is
our independent ledger for the same signals: the Pine scripts in ``pine/``
emit a small JSON payload with every fill, TradingView POSTs it to
``http://<host>:<port>/alert/<token>``, and this server

1. authenticates the request by the secret token in the URL path,
2. records the signal in a CSV ledger, and
3. re-executes the trade on *simulation money* using this project's own risk
   rules (stop-based sizing, max drawdown halt), so you can watch a second,
   independent equity curve.

By design there is no code path to a real broker: everything here is
bookkeeping on simulation money.
"""

from __future__ import annotations

import csv
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

#: Incoming ``action`` values we understand.  Long-only, like the engine.
ACTION_ALIASES: dict[str, str] = {
    "buy": "buy", "long": "buy", "entry": "buy", "enter": "buy", "b": "buy",
    "close": "close", "sell": "close", "exit": "close", "flat": "close", "s": "close",
}


class AlertError(ValueError):
    """Raised for malformed or unauthenticated alert payloads."""


@dataclass(frozen=True, slots=True)
class Alert:
    action: str
    symbol: str
    price: float
    stop: float | None = None
    reason: str = ""


def parse_alert(payload: bytes | str | dict[str, Any]) -> Alert:
    """Parse a TradingView alert body into an :class:`Alert`.

    Accepts the JSON emitted by the ``pine/`` scripts and a few common
    shorthand forms (``action``/``side``, ``price``/``close``).
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            raise AlertError("empty alert body")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AlertError(f"alert body is not valid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise AlertError("alert body must be a json object")

    raw_action = str(payload.get("action") or payload.get("side") or "").strip().lower()
    action = ACTION_ALIASES.get(raw_action)
    if action is None:
        raise AlertError(f"unknown action {raw_action!r}; expected one of {sorted(set(ACTION_ALIASES))}")

    raw_price = payload.get("price", payload.get("close"))
    if raw_price is None:
        raise AlertError("alert is missing 'price'")
    try:
        price = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise AlertError(f"bad price {raw_price!r}") from exc
    if not price > 0:
        raise AlertError(f"price must be positive, got {price}")

    stop: float | None = None
    if payload.get("stop") is not None:
        try:
            stop = float(payload["stop"])
        except (TypeError, ValueError) as exc:
            raise AlertError(f"bad stop {payload['stop']!r}") from exc

    return Alert(
        action=action,
        symbol=str(payload.get("symbol") or "unknown"),
        price=price,
        stop=stop,
        reason=str(payload.get("reason") or ""),
    )


@dataclass(frozen=True, slots=True)
class MirrorFill:
    ts: datetime
    action: str
    symbol: str
    price: float
    qty: float
    fee: float
    cash: float
    equity: float
    halted: bool
    note: str


class MirrorAccount:
    """Re-executes alert signals on simulation money with this repo's risk rules.

    Mirrors the semantics of ``broker.Broker``/``risk.RiskManager`` in spirit:
    stop-based sizing, a max-position cap, and a max-drawdown halt on new
    entries.  Fees default to zero because TradingView's paper trading has
    already charged its own simulated commission; the mirror is a shadow ledger.
    """

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        risk_per_trade: float = 0.01,
        max_position_pct: float = 0.95,
        max_drawdown_stop: float = 0.10,
        fee_bps: float = 0.0,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.qty = 0.0
        self.avg_price = 0.0
        self.symbol = ""
        self.risk_per_trade = risk_per_trade
        self.max_position_pct = max_position_pct
        self.max_drawdown_stop = max_drawdown_stop
        self.fee_bps = fee_bps
        self.peak_equity = initial_cash
        self.halted = False
        self._lock = threading.Lock()

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def _check_drawdown(self, price: float) -> None:
        eq = self.equity(price)
        self.peak_equity = max(self.peak_equity, eq)
        if not self.halted and self.peak_equity > 0:
            dd = 1.0 - eq / self.peak_equity
            if dd >= self.max_drawdown_stop:
                self.halted = True

    def _fee(self, notional: float) -> float:
        return notional * self.fee_bps / 10_000.0

    def apply(self, alert: Alert, *, ts: datetime | None = None) -> MirrorFill | None:
        """Execute an alert; returns a fill record, or ``None`` if ignored."""
        ts = ts or datetime.now(timezone.utc)
        with self._lock:
            self._check_drawdown(alert.price)

            if alert.action == "buy":
                if self.halted:
                    return MirrorFill(ts, alert.action, alert.symbol, alert.price, 0.0, 0.0,
                                      self.cash, self.equity(alert.price), True, "halted - buy ignored")
                if self.qty > 0:
                    return MirrorFill(ts, alert.action, alert.symbol, alert.price, 0.0, 0.0,
                                      self.cash, self.equity(alert.price), False, "already long - ignored")
                if self.symbol and alert.symbol != self.symbol:
                    return MirrorFill(ts, alert.action, alert.symbol, alert.price, 0.0, 0.0,
                                      self.cash, self.equity(alert.price), False,
                                      f"different symbol {alert.symbol} (tracking {self.symbol}) - ignored")
                equity = self.equity(alert.price)
                stop = alert.stop if alert.stop is not None and alert.stop < alert.price else None
                if stop is not None:
                    stop_dist = alert.price - stop
                    risk_amt = equity * self.risk_per_trade
                    qty = risk_amt / stop_dist
                else:
                    qty = equity * self.max_position_pct / alert.price
                qty = min(qty, equity * self.max_position_pct / alert.price)
                notional = qty * alert.price
                fee = self._fee(notional)
                self.cash -= notional + fee
                self.qty = qty
                self.avg_price = alert.price
                self.symbol = alert.symbol
                return MirrorFill(ts, alert.action, alert.symbol, alert.price, qty, fee,
                                  self.cash, self.equity(alert.price), self.halted,
                                  "stop-sized" if stop is not None else "no stop in payload")

            # close
            if self.qty <= 0:
                return MirrorFill(ts, alert.action, alert.symbol, alert.price, 0.0, 0.0,
                                  self.cash, self.equity(alert.price), self.halted, "flat - ignored")
            notional = self.qty * alert.price
            fee = self._fee(notional)
            self.cash += notional - fee
            closed_qty, self.qty = self.qty, 0.0
            avg = self.avg_price
            self.avg_price = 0.0
            self.symbol = ""
            pnl = (alert.price - avg) * closed_qty - fee
            return MirrorFill(ts, alert.action, alert.symbol, alert.price, -closed_qty, fee,
                              self.cash, self.equity(alert.price), self.halted,
                              f"closed (pnl {pnl:+.2f})")


ALERT_CSV_COLUMNS = [
    "ts", "symbol", "action", "price", "stop", "qty", "fee", "cash", "equity", "halted", "note",
]


def append_alert_row(path: str | Path, fill: MirrorFill) -> Path:
    """Append one mirrored fill to the alert ledger CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(ALERT_CSV_COLUMNS)
        writer.writerow([
            fill.ts.isoformat(), fill.symbol, fill.action, f"{fill.price:.8f}",
            "" , f"{fill.qty:.8f}", f"{fill.fee:.8f}", f"{fill.cash:.2f}",
            f"{fill.equity:.2f}", fill.halted, fill.note,
        ])
    return path


class AlertServer:
    """HTTP server accepting TradingView webhooks at ``/alert/<token>``."""

    def __init__(
        self,
        token: str,
        mirror: MirrorAccount | None = None,
        *,
        ledger: str | Path = "runs/alerts.csv",
        host: str = "127.0.0.1",
        port: int = 8788,
    ) -> None:
        if not token or "/" in token:
            raise ValueError("token must be a non-empty string without '/'")
        self.token = token
        self.mirror = mirror or MirrorAccount()
        self.ledger = Path(ledger)
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    # -- request handling ---------------------------------------------------
    def handle(self, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Process one request; returns ``(status, response_json)``. Testable."""
        if path != f"/alert/{self.token}":
            return 404, {"ok": False, "error": "not found"}
        if method != "POST":
            return 405, {"ok": False, "error": "POST only"}
        try:
            alert = parse_alert(body)
        except AlertError as exc:
            return 400, {"ok": False, "error": str(exc)}
        fill = self.mirror.apply(alert)
        assert fill is not None
        append_alert_row(self.ledger, fill)
        print(
            f"[alerts] {fill.ts:%Y-%m-%d %H:%M:%S} {fill.action.upper():<5} {fill.symbol} "
            f"@ {fill.price:.4f} qty={fill.qty:.6f} equity={fill.equity:,.2f} :: {fill.note}",
            flush=True,
        )
        return 200, {
            "ok": True,
            "equity": fill.equity,
            "qty": fill.qty,
            "halted": fill.halted,
            "note": fill.note,
        }

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                status, payload = server.handle(self.command, self.path, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # keep the console to our own [alerts] lines

        return Handler

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> ThreadingHTTPServer:
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        return self._httpd

    def serve_forever(self) -> None:
        httpd = self.start()
        print(f"[alerts] listening on http://{self.host}:{httpd.server_port}/alert/{self.token}")
        print(f"[alerts] ledger: {self.ledger}   simulation cash: {self.mirror.initial_cash:,.2f}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


def random_token() -> str:
    """A fresh URL-safe token for the webhook path."""
    return secrets.token_urlsafe(16)
