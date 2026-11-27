"""Binance **spot** broker — demo trading rehearsal first, production later.

Two base URLs, one contract:

* https://demo-api.binance.com — Binance demo trading: real API surface, real
  signing, real order types, virtual balances. The default.
* https://api.binance.com — production spot.

Market data never comes from here. The bot reads production klines through
bot.data.binance and only *routes orders* through this client. Demo's tape is
its own book (closed opens/closes match production, highs, lows and volume do
not), so signalling off it would quietly change the strategy.

Spot only is enforced in code: every request must live under /api/v3/.
Futures (/fapi), margin (/sapi) and every wallet endpoint are rejected before a
socket opens.

Order ids are idempotent: make_client_order_id derives a deterministic id from
the signal, so a restart retries the *same* order instead of a second one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import Balance, BrokerError, OrderResult, SymbolFilters

DEMO_BASE = "https://demo-api.binance.com"
PROD_BASE = "https://api.binance.com"

SPOT_PREFIX = "/api/v3/"
CLIENT_ORDER_ID_MAX = 36


def sanitize_client_order_id(raw: str) -> str:
    """Binance accepts alphanumerics, '-' and '_' up to 36 chars."""
    return "".join(ch for ch in str(raw) if ch.isalnum()) [:CLIENT_ORDER_ID_MAX]


def make_client_order_id(tag: str, bar_ts_ms: int, seq: int = 0) -> str:
    """Deterministic, alphanumeric order id (Binance allows alnum, '-' and '_')."""
    clean = "".join(ch for ch in str(tag) if ch.isalnum())[:8]
    return sanitize_client_order_id(f"tb{clean}{int(bar_ts_ms)}{int(seq)}")


def _default_transport(method: str, url: str, headers: dict[str, str],
                       body: bytes | None, timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"msg": raw[:300]}
        return e.code, payload
    except urllib.error.URLError as e:
        raise BrokerError(f"binance transport error: {e.reason}", payload={"url": url}) from e


class BinanceSpotBroker:
    """Spot-only client. Defaults to demo trading; production is opt-in."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = DEMO_BASE,
        transport: Callable[..., tuple[int, Any]] | None = None,
        clock: Callable[[], float] = time.time,
        timeout: float = 20.0,
        recv_window: int = 5000,
    ):
        if not api_key or not api_secret:
            raise BrokerError("binance api key and secret are required")
        if base_url.rstrip("/").endswith("testnet.binance.vision"):
            raise BrokerError(
                "testnet is not supported: its book is ~1/70th the real volume; "
                "use demo-api.binance.com"
            )
        self.api_key = api_key
        self._secret = api_secret.encode()
        self.base_url = base_url.rstrip("/")
        self._transport = transport or _default_transport
        self._clock = clock
        self.timeout = timeout
        self.recv_window = recv_window
        self._filters: dict[str, SymbolFilters] = {}
        self.is_demo = self.base_url == DEMO_BASE

    # ---------------------------------------------------------------- plumbing
    def _url(self, path: str, query: str = "") -> str:
        if not path.startswith(SPOT_PREFIX):
            raise ValueError(
                f"spot-only broker: refusing path {path!r}; only {SPOT_PREFIX}* is allowed"
            )
        return f"{self.base_url}{path}" + (f"?{query}" if query else "")

    def _sign(self, query: str) -> str:
        return hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()

    def _call(self, method: str, path: str, params: dict[str, Any] | None = None,
              *, signed: bool = True) -> Any:
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(self._clock() * 1000)
            params["recvWindow"] = self.recv_window
        query = urllib.parse.urlencode(params, doseq=True)
        if signed:
            query = f"{query}&signature={self._sign(query)}"
        headers = {"X-MBX-APIKEY": self.api_key} if signed else {}
        status, payload = self._transport(
            method, self._url(path, query), headers, None, self.timeout
        )
        if status >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            msg = payload.get("msg") if isinstance(payload, dict) else str(payload)[:200]
            raise BrokerError(f"binance {path} -> {status}: {msg}", code=code, payload=payload)
        return payload

    # ---------------------------------------------------------------- account
    def account(self) -> dict:
        return self._call("GET", "/api/v3/account")

    def balances(self, *, nonzero: bool = True) -> dict[str, Balance]:
        out: dict[str, Balance] = {}
        for row in self.account().get("balances", []):
            bal = Balance(row["asset"], float(row["free"]), float(row["locked"]))
            if not nonzero or bal.total > 0:
                out[bal.asset] = bal
        return out

    def spot_usdt(self) -> float:
        return self.balances().get("USDT", Balance("USDT", 0.0)).free

    # ---------------------------------------------------------------- rules
    def filters(self, symbol: str) -> SymbolFilters:
        sym = symbol.upper()
        if sym not in self._filters:
            info = self._call("GET", "/api/v3/exchangeInfo", {"symbol": sym}, signed=False)
            rows = info.get("symbols") or []
            if not rows:
                raise BrokerError(f"unknown symbol {symbol!r}")
            row = rows[0]
            step = tick = 0.0
            min_qty = min_notional = 0.0
            for f in row.get("filters", []):
                kind = f.get("filterType")
                if kind == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f.get("minQty", 0.0))
                elif kind == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif kind in {"NOTIONAL", "MIN_NOTIONAL"}:
                    min_notional = float(f.get("minNotional", 0.0))
            self._filters[sym] = SymbolFilters(
                symbol=row.get("symbol", sym), step_size=step, tick_size=tick,
                min_qty=min_qty, min_notional=min_notional,
            )
        return self._filters[sym]

    # ---------------------------------------------------------------- orders
    def open_orders(self, symbol: str | None = None) -> list[dict]:
        params = {"symbol": symbol.upper()} if symbol else {}
        return self._call("GET", "/api/v3/openOrders", params)

    def my_trades(self, symbol: str, *, limit: int = 100) -> list[dict]:
        return self._call("GET", "/api/v3/myTrades", {"symbol": symbol.upper(), "limit": limit})

    def place_order(
        self,
        symbol: str,
        side: str,
        *,
        quantity: float | None = None,
        quote_quantity: float | None = None,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_price: float | None = None,
        time_in_force: str | None = None,
        client_order_id: str | None = None,
    ) -> OrderResult:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if (quantity is None) == (quote_quantity is None):
            raise ValueError("pass exactly one of quantity / quote_quantity")

        rules = self.filters(symbol)
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": order_type.upper(),
        }
        if quote_quantity is not None:
            qty_quote = float(quote_quantity)
            if rules.min_notional and qty_quote < rules.min_notional:
                raise BrokerError(
                    f"{symbol} min notional is {rules.min_notional}, got {qty_quote}"
                )
            params["quoteOrderQty"] = f"{qty_quote:.8f}".rstrip("0").rstrip(".")
        else:
            qty = rules.floor_qty(float(quantity or 0.0))
            if qty <= 0 or (rules.min_qty and qty < rules.min_qty):
                raise BrokerError(
                    f"{symbol} quantity {quantity} floors to {qty} "
                    f"(min {rules.min_qty}, step {rules.step_size})"
                )
            params["quantity"] = f"{qty:.8f}".rstrip("0").rstrip(".")
        if price is not None:
            params["price"] = f"{rules.floor_price(float(price)):.8f}".rstrip("0").rstrip(".")
        if stop_price is not None:
            params["stopPrice"] = f"{rules.floor_price(float(stop_price)):.8f}".rstrip("0").rstrip(".")
        if time_in_force:
            params["timeInForce"] = time_in_force
        if client_order_id:
            params["newClientOrderId"] = sanitize_client_order_id(client_order_id)

        raw = self._call("POST", "/api/v3/order", params)
        return OrderResult(
            symbol=raw.get("symbol", symbol.upper()),
            side=raw.get("side", side),
            order_type=raw.get("type", order_type.upper()),
            order_id=raw.get("orderId"),
            client_order_id=raw.get("clientOrderId"),
            status=raw.get("status", ""),
            executed_qty=float(raw.get("executedQty", 0.0) or 0.0),
            quote_qty=float(raw.get("cummulativeQuoteQty", 0.0) or 0.0),
            price=float(raw["price"]) if raw.get("price") not in (None, "", "0.00000000") else None,
            raw=raw,
        )

    def market_buy(self, symbol: str, quote_quantity: float, *,
                   client_order_id: str | None = None) -> OrderResult:
        return self.place_order(symbol, "BUY", quote_quantity=quote_quantity,
                                client_order_id=client_order_id)

    def market_sell(self, symbol: str, quantity: float, *,
                    client_order_id: str | None = None) -> OrderResult:
        return self.place_order(symbol, "SELL", quantity=quantity,
                                client_order_id=client_order_id)

    def close_position(self, symbol: str, asset: str, *,
                       client_order_id: str | None = None) -> OrderResult | None:
        """Sell the whole free balance of a spot asset (no shorts, by design)."""
        free = self.balances().get(asset.upper(), Balance(asset, 0.0)).free
        if free <= 0:
            return None
        return self.market_sell(symbol, free, client_order_id=client_order_id)

    def cancel_order(self, symbol: str, *, order_id: int | str | None = None,
                     client_order_id: str | None = None) -> dict:
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        elif client_order_id is not None:
            params["origClientOrderId"] = sanitize_client_order_id(client_order_id)
        else:
            raise ValueError("pass order_id or client_order_id")
        return self._call("DELETE", "/api/v3/order", params)
