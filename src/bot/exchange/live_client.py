"""Live trading client for Binance spot: production signals, demo orders.

The live loop needs five things from a venue: bars, equity, position size,
an order, and a flatten.  This module is that surface for Binance spot:

* **bars** always come from production klines (``api.binance.com``), because
  the demo book is thin and its candles must never drive a signal;
* **orders** always go to the demo endpoint (``demo-api.binance.com``) with
  ``BINANCE_DEMO_KEY`` / ``BINANCE_DEMO_SECRET``.  There is no code path to
  production order routing here - the broker itself refuses non-``/api/v3``
  paths and defaults to the demo base URL.

Spot has no bracket orders, so stops are managed by the caller (LiveTrader
keeps them in its state file and flattens on the first bar that trades
through the stop).  Symbol forms accepted everywhere: ``BTC/USDT``,
``BTC/USD`` (mapped to USDT), ``BTCUSDT``, ``btc-usdt``.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..data.binance import INTERVALS, fetch_binance_bars, fetch_price
from ..data.feed import Bar
from ..envdot import load_env, load_env_for
from .binance import DEMO_BASE, BinanceSpotBroker, BrokerError

#: Quote assets we can split off a symbol without a separator.
QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "BNB")

#: Stablecoins that count as cash rather than as a position.
CASH_ASSETS = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD"}

#: Timeframe alias -> (binance interval, bars aggregated into one).
INTERVAL_ALIASES: dict[str, tuple[str, int]] = {
    "1m": ("1m", 1), "3m": ("3m", 1), "5m": ("5m", 1),
    "15m": ("15m", 1), "30m": ("30m", 1),
    "1h": ("1h", 1), "1Hour": ("1h", 1),
    "2h": ("2h", 1), "4h": ("4h", 1), "4Hour": ("4h", 1),
    "6h": ("6h", 1), "8h": ("8h", 1), "12h": ("12h", 1),
    "1d": ("1d", 1), "1Day": ("1d", 1), "d": ("1d", 1),
    "3d": ("3d", 1), "1w": ("1w", 1), "1Week": ("1w", 1), "w": ("1w", 1),
    "1M": ("1M", 1), "1Month": ("1M", 1), "M": ("1M", 1),
}


def interval_for(timeframe: str) -> tuple[str, int]:
    """Map a CLI/web timeframe alias to (binance interval, aggregate factor)."""
    key = timeframe.strip()
    if key not in INTERVAL_ALIASES:
        valid = ", ".join(sorted(INTERVAL_ALIASES))
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of: {valid}")
    interval, factor = INTERVAL_ALIASES[key]
    if interval not in INTERVALS:
        raise ValueError(f"interval {interval!r} is not fetchable")
    return interval, factor


def duration_for(timeframe: str) -> timedelta:
    """How much clock time one bar of ``timeframe`` spans."""
    interval, factor = interval_for(timeframe)
    return INTERVALS[interval] * factor


def split_symbol(symbol: str) -> tuple[str, str, str]:
    """Return (base asset, quote asset, binance pair) for any accepted form."""
    raw = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in raw:
        base, _, quote = raw.partition("/")
    else:
        for quote in QUOTE_SUFFIXES:
            if raw.endswith(quote) and len(raw) > len(quote):
                base = raw[: -len(quote)]
                break
        else:
            raise ValueError(f"cannot split {symbol!r} into base and quote assets")
    if not base or not quote:
        raise ValueError(f"cannot split {symbol!r} into base and quote assets")
    # Binance spot has no USD pairs; BTC/USD is traded as BTCUSDT.  This is the
    # same convention the web catalog uses ("BTC/USD" -> BINANCE:BTCUSDT).
    if quote == "USD":
        quote = "USDT"
    return base, quote, f"{base}{quote}"


class BinanceLiveClient:
    """Duck-typed venue client used by LiveTrader, the CLI and the dashboard."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        env_file: str | None = None,
        env: str | None = None,
        broker: BinanceSpotBroker | None = None,
        base_url: str = DEMO_BASE,
        timeout: float = 20.0,
    ) -> None:
        from ..envcfg import env_name as configured_env
        from ..envdot import load_env_for

        self.env = (env or configured_env()).strip().lower()
        if env_file is not None:
            load_env(env_file)
        else:
            load_env_for(self.env)

        # Rail 1: prod may never hold or display a demo wallet.  Demo keys are
        # virtual money; seeing them under a PRODUCTION banner is the exact
        # mistake that teaches people to trust the wrong number.
        if self.env == "prod":
            raise BrokerError(
                "PROD refused: this build has no live venue. Demo credentials are "
                "never accepted in prod, and real-money routing must be built and "
                "reviewed deliberately. No balances can be read and no order can "
                "be placed here yet."
            )
        self.api_key = api_key or os.environ.get("BINANCE_DEMO_KEY") or ""
        self.api_secret = api_secret or os.environ.get("BINANCE_DEMO_SECRET") or ""
        if broker is None:
            if not self.api_key or not self.api_secret:
                raise BrokerError(
                    "Missing Binance demo credentials. Set BINANCE_DEMO_KEY and "
                    "BINANCE_DEMO_SECRET in .env.paper (server) or .env (laptop); "
                    "create them under Binance -> API Management -> demo trading."
                )
            broker = BinanceSpotBroker(
                self.api_key, self.api_secret, base_url=base_url, timeout=timeout
            )
        self.broker = broker
        self.is_demo = bool(getattr(broker, "is_demo", base_url == DEMO_BASE))
        # Ticker prices are public and cheap, but a poll loop must not hammer
        # the endpoint for every balance; 30 seconds is plenty for sizing.
        self._prices: dict[str, tuple[float, float]] = {}
        self._costs: dict[str, tuple[float, float, float]] = {}

    # -- market data --------------------------------------------------------
    def fetch_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10_000,
    ) -> list[Bar]:
        """OHLCV bars from production Binance, oldest first."""
        _, _, pair = split_symbol(symbol)
        interval, _ = interval_for(timeframe)
        limit = max(1, int(limit))
        if start is None and end is None and limit > 1000:
            # One page is 1000 rows; ask from the window start so the pager
            # walks forward instead of truncating to the newest page.
            start = datetime.now(timezone.utc) - INTERVALS[interval] * limit
        rows = fetch_binance_bars(pair, interval, start=start, end=end, limit=limit)
        return rows[-limit:]

    def _price(self, asset: str) -> float:
        """Last USDT price of ``asset`` (cached 30s); 1.0 for stablecoins."""
        asset = asset.upper()
        if asset in CASH_ASSETS:
            return 1.0
        cached = self._prices.get(asset)
        now = time.time()
        if cached and now - cached[1] < 30:
            return cached[0]
        price = fetch_price(f"{asset}USDT")
        self._prices[asset] = (price, now)
        return price

    # -- account ------------------------------------------------------------
    def get_account(self) -> dict[str, Any]:
        """USDT value of the demo wallet, shaped like the old account payload."""
        balances = self.broker.balances()
        cash = 0.0
        positions_value = 0.0
        for asset, bal in balances.items():
            if asset in CASH_ASSETS:
                cash += bal.total
            else:
                try:
                    positions_value += bal.total * self._price(asset)
                except Exception:  # noqa: BLE001 - an unpriced dust asset is not fatal
                    continue
        return {
            "account_number": "binance-demo" if self.is_demo else "binance-live",
            "status": "DEMO" if self.is_demo else "LIVE",
            "currency": "USDT",
            "equity": cash + positions_value,
            "cash": cash,
            "buying_power": cash,
            "positions_value": positions_value,
        }

    def _avg_cost(self, asset: str, pair: str) -> tuple[float, float]:
        """(average entry price, unrealized pnl) from the demo trade history."""
        cached = self._costs.get(pair)
        now = time.time()
        if cached and now - cached[2] < 300:
            return cached[0], cached[1]
        qty = 0.0
        cost = 0.0
        try:
            trades = self.broker.my_trades(pair, limit=1000)
        except BrokerError:
            trades = []
        for t in sorted(trades, key=lambda r: int(r.get("time") or 0)):
            q = float(t.get("qty") or 0)
            quote = float(t.get("quoteQty") or 0)
            if t.get("isBuyer"):
                qty += q
                cost += quote
            elif qty > 0:
                closed = min(q, qty)
                cost -= (cost / qty) * closed
                qty -= closed
        avg = cost / qty if qty > 0 else 0.0
        price = 0.0
        try:
            price = self._price(asset)
        except Exception:  # noqa: BLE001
            price = 0.0
        pnl = (price - avg) * qty if qty > 0 and price else 0.0
        self._costs[pair] = (avg, pnl, now)
        return avg, pnl

    def get_positions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for asset, bal in self.broker.balances().items():
            if asset in CASH_ASSETS or bal.total <= 0:
                continue
            pair = f"{asset}USDT"
            try:
                price = self._price(asset)
            except Exception:  # noqa: BLE001
                continue
            avg, pnl = self._avg_cost(asset, pair)
            rows.append({
                "symbol": f"{asset}/USDT",
                "qty": bal.total,
                "avg_entry_price": avg,
                "current_price": price,
                "market_value": bal.total * price,
                "unrealized_pl": pnl,
            })
        return rows

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        base, _, _ = split_symbol(symbol)
        bal = self.broker.balances().get(base)
        if bal is None or bal.total <= 0:
            return None
        return {"symbol": symbol, "qty": bal.total}

    # -- orders ------------------------------------------------------------
    def filters(self, symbol: str):
        """Venue lot and tick rules for one pair, cached by the broker.

        The loop asks for these to tell a real position from a leftover it
        could never sell (a balance below the lot step is dust, not a book).
        """
        base, _, pair = split_symbol(symbol)
        return self.broker.filters(pair)

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        *,
        price: float | None = None,
        stop: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        """Market order on the demo venue.  ``stop``/``take_profit`` are
        accepted for interface parity but managed locally: spot has no
        bracket orders (the caller keeps the stop in its state file)."""
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy/sell, got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        base, _, pair = split_symbol(symbol)
        if side == "buy":
            quote_qty = qty * (price if price else self._price(base))
            order = self.broker.market_buy(pair, quote_qty)
        else:
            order = self.broker.market_sell(pair, qty)
        return {
            "id": str(order.order_id) if order.order_id is not None else "",
            "symbol": pair,
            "side": side,
            "status": order.status,
            "filled_qty": order.executed_qty,
            "filled_avg_price": (
                order.quote_qty / order.executed_qty if order.executed_qty else None
            ),
            "raw": order.raw,
        }

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Resting orders on the venue.  This desk sends market orders only,
        so normally empty; a manual order on the same pair would show here."""
        _, _, pair = split_symbol(symbol)
        out: list[dict[str, Any]] = []
        for o in self.broker.open_orders(pair):
            out.append({
                "id": str(o.get("orderId") or ""),
                "side": str(o.get("side") or "").lower(),
                "type": o.get("type"),
                "qty": float(o.get("origQty") or 0),
                "filled": float(o.get("executedQty") or 0),
                "price": float(o.get("price") or 0),
                "stop_price": float(o.get("stopPrice") or 0),
                "status": o.get("status"),
                "created_at": datetime.fromtimestamp(
                    int(o.get("time") or 0) / 1000.0, tz=timezone.utc
                ).isoformat(),
            })
        return out

    def close_position(self, symbol: str) -> dict[str, Any] | None:
        base, _, pair = split_symbol(symbol)
        order = self.broker.close_position(pair, base)
        if order is None:
            return None
        return {"id": str(order.order_id or ""), "symbol": pair, "status": order.status}

    # -- history ------------------------------------------------------------
    def closed_fills(self, symbols: list[str], *, per_symbol: int = 500) -> list[dict[str, Any]]:
        """Normalized trade rows for the dashboard (``get_fills`` shape)."""
        fills: list[dict[str, Any]] = []
        for symbol in symbols:
            _, _, pair = split_symbol(symbol)
            try:
                trades = self.broker.my_trades(pair, limit=per_symbol)
            except BrokerError:
                continue
            for t in trades:
                fills.append({
                    "order_id": str(t.get("id") or ""),
                    "symbol": f"{split_symbol(symbol)[0]}/USDT",
                    "side": "buy" if t.get("isBuyer") else "sell",
                    "qty": float(t.get("qty") or 0),
                    "price": float(t.get("price") or 0),
                    "filled_at": datetime.fromtimestamp(
                        int(t.get("time") or 0) / 1000.0, tz=timezone.utc
                    ).isoformat(),
                    "bot_name": (t.get("clientOrderId") or "").split(":")[0][:60],
                })
        fills.sort(key=lambda f: f["filled_at"])
        return fills


__all__ = [
    "BinanceLiveClient", "interval_for", "duration_for", "split_symbol",
    "INTERVAL_ALIASES",
]
