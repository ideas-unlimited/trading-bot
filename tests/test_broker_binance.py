"""Tests for the Binance spot exchange client (no network: fake transport)."""

from __future__ import annotations

import hashlib
import hmac
import unittest
import urllib.parse

from bot.exchange import Balance, BrokerError, SymbolFilters
from bot.exchange.binance import (
    DEMO_BASE,
    PROD_BASE,
    BinanceSpotBroker,
    make_client_order_id,
)

KEY = "test-key"
SECRET = "test-secret"

INFO = {
    "symbols": [{
        "symbol": "BTCUSDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001000"},
            {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
        ],
    }]
}

ACK_SELL = {
    "symbol": "BTCUSDT", "orderId": 4242, "clientOrderId": "tbabc1",
    "status": "FILLED", "executedQty": "0.00123", "cummulativeQuoteQty": "73.10",
    "price": "0.00000000", "side": "SELL", "type": "MARKET",
}
ACK_BUY = {**ACK_SELL, "side": "BUY", "executedQty": "0.00415",
           "cummulativeQuoteQty": "250.00", "orderId": 4243}


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers})
        return self.responses.pop(0) if self.responses else (200, {})

    def query(self, index=-1):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.calls[index]["url"]).query)


def broker(responses=None, **kw):
    transport = FakeTransport(responses)
    client = BinanceSpotBroker(KEY, SECRET, transport=transport,
                               clock=lambda: 1_700_000_000.0, **kw)
    return client, transport


class ConstructionTests(unittest.TestCase):
    def test_defaults_to_demo(self):
        client, _ = broker()
        self.assertEqual(client.base_url, DEMO_BASE)
        self.assertTrue(client.is_demo)

    def test_production_is_explicit(self):
        client, _ = broker(base_url=PROD_BASE)
        self.assertFalse(client.is_demo)

    def test_requires_credentials(self):
        with self.assertRaises(BrokerError):
            BinanceSpotBroker("", "")
        with self.assertRaises(BrokerError):
            BinanceSpotBroker(KEY, "")

    def test_testnet_is_refused(self):
        with self.assertRaises(BrokerError):
            BinanceSpotBroker(KEY, SECRET, base_url="https://testnet.binance.vision")


class SigningTests(unittest.TestCase):
    def test_signature_is_hmac_of_exact_query(self):
        client, transport = broker([(200, {"balances": []})])
        client.account()
        call = transport.calls[0]
        query = urllib.parse.urlparse(call["url"]).query
        body, _, sig = query.rpartition("&signature=")
        expected = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(sig, expected)
        self.assertIn("timestamp=1700000000000", body)
        self.assertIn("recvWindow=5000", body)
        self.assertEqual(call["headers"]["X-MBX-APIKEY"], KEY)
        self.assertEqual(call["method"], "GET")

    def test_public_calls_are_unsigned(self):
        client, transport = broker([(200, INFO)])
        client.filters("BTCUSDT")
        query = urllib.parse.urlparse(transport.calls[0]["url"]).query
        self.assertNotIn("signature", query)
        self.assertEqual(transport.calls[0]["headers"], {})


class SpotOnlyTests(unittest.TestCase):
    def test_rejects_futures_and_margin_paths(self):
        client, transport = broker()
        for path in ("/fapi/v1/order", "/sapi/v1/margin/order", "/dapi/v1/order"):
            with self.assertRaises(ValueError):
                client._call("POST", path, {})
        self.assertEqual(transport.calls, [])

    def test_allows_spot_paths(self):
        client, _ = broker()
        self.assertTrue(client._url("/api/v3/order").endswith("/api/v3/order"))


class FilterTests(unittest.TestCase):
    def test_floors_quantity_to_step(self):
        rules = SymbolFilters("BTCUSDT", step_size=0.00001, tick_size=0.01,
                              min_qty=0.00001, min_notional=5.0)
        self.assertAlmostEqual(rules.floor_qty(0.00123456), 0.00123, places=12)
        self.assertAlmostEqual(rules.floor_price(61234.567), 61234.56, places=12)

    def test_filters_are_cached(self):
        client, transport = broker([(200, INFO), (200, ACK_BUY), (200, ACK_BUY)])
        client.market_buy("BTCUSDT", 250.0)
        client.market_buy("BTCUSDT", 250.0)
        exchange_calls = [c for c in transport.calls if "exchangeInfo" in c["url"]]
        self.assertEqual(len(exchange_calls), 1)


class OrderTests(unittest.TestCase):
    def test_market_buy_uses_quote_order_qty(self):
        client, transport = broker([(200, INFO), (200, ACK_BUY)])
        result = client.market_buy("BTCUSDT", 250.0, client_order_id="tb-btc-1")
        q = transport.query()
        self.assertEqual(q["quoteOrderQty"], ["250"])
        self.assertNotIn("quantity", q)
        self.assertEqual(q["side"], ["BUY"])
        self.assertEqual(q["newClientOrderId"], ["tbbtc1"])
        self.assertEqual(result.order_id, 4243)
        self.assertTrue(result.filled)

    def test_market_sell_floors_and_truncates(self):
        client, transport = broker([(200, INFO), (200, ACK_SELL)])
        client.market_sell("BTCUSDT", 0.00123456)
        q = transport.query()
        self.assertEqual(q["quantity"], ["0.00123"])
        self.assertEqual(q["type"], ["MARKET"])

    def test_below_min_notional_never_reaches_the_api(self):
        client, transport = broker([(200, INFO)])
        with self.assertRaises(BrokerError):
            client.market_buy("BTCUSDT", 3.0)
        self.assertEqual(len(transport.calls), 1)

    def test_quantity_below_min_is_refused(self):
        client, transport = broker([(200, INFO)])
        with self.assertRaises(BrokerError):
            client.market_sell("BTCUSDT", 0.0000001)
        self.assertEqual(len(transport.calls), 1)

    def test_api_error_becomes_broker_error(self):
        client, _ = broker([(200, INFO),
                            (400, {"code": -2010, "msg": "insufficient balance"})])
        with self.assertRaises(BrokerError) as ctx:
            client.market_sell("BTCUSDT", 0.001)
        self.assertEqual(ctx.exception.code, -2010)
        self.assertIn("insufficient balance", str(ctx.exception))

    def test_cancel_by_client_order_id(self):
        client, transport = broker([(200, {"status": "CANCELED"})])
        client.cancel_order("BTCUSDT", client_order_id="tb-btc-1")
        q = transport.query()
        self.assertEqual(q["origClientOrderId"], ["tbbtc1"])
        self.assertEqual(transport.calls[0]["method"], "DELETE")

    def test_close_position_sells_the_free_balance(self):
        account = {"balances": [{"asset": "BTC", "free": "0.5", "locked": "0"}]}
        client, transport = broker([(200, account), (200, INFO), (200, ACK_SELL)])
        result = client.close_position("BTCUSDT", "BTC")
        self.assertIsNotNone(result)
        self.assertEqual(transport.query()["quantity"], ["0.5"])

    def test_close_position_when_flat_returns_none(self):
        account = {"balances": []}
        client, transport = broker([(200, account)])
        self.assertIsNone(client.close_position("BTCUSDT", "BTC"))
        self.assertEqual(len(transport.calls), 1)


class ClientOrderIdTests(unittest.TestCase):
    def test_deterministic_and_sanitised(self):
        a = make_client_order_id("momentum_swing", 1_700_000_000_000, 0)
        b = make_client_order_id("momentum_swing", 1_700_000_000_000, 0)
        self.assertEqual(a, b)
        self.assertTrue(a.isalnum())
        self.assertLessEqual(len(a), 36)
        self.assertTrue(a.startswith("tb"))

    def test_changes_with_bar_and_sequence(self):
        ids = {
            make_client_order_id("m", 1_700_000_000_000, 0),
            make_client_order_id("m", 1_700_000_000_000, 1),
            make_client_order_id("m", 1_700_014_400_000, 0),
        }
        self.assertEqual(len(ids), 3)

    def test_balances_parse_locked_funds(self):
        account = {"balances": [{"asset": "USDT", "free": "100.5", "locked": "2.5"},
                                {"asset": "BTC", "free": "0", "locked": "0"}]}
        client, _ = broker([(200, account)])
        bals = client.balances()
        self.assertEqual(bals["USDT"], Balance("USDT", 100.5, 2.5))
        self.assertNotIn("BTC", bals)


if __name__ == "__main__":
    unittest.main()