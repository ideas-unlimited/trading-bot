"""Tests for outbound trade alerts (no network; channels are faked)."""

import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.message import EmailMessage
from unittest import mock

from bot.broker import Fill
from bot.forward import _fill_key, _notify_fills
from bot.notify import (
    EmailChannel, Notifier, NtfyChannel, TelegramChannel, format_trade,
)


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Capture:
    name = "capture"

    def __init__(self, ok=True):
        self.enabled = True
        self.ok = ok
        self.calls = []

    def send_trade(self, event):
        self.calls.append(dict(event))
        return self.ok


def notify(previous, fills, notifier, trades=()):
    return _notify_fills(
        notifier=notifier,
        previous=previous,
        fills=fills,
        trades=trades,
        strategy="capitulation_dip_uni",
        symbol="UNI/USDT",
        timeframe="4h",
        now=T0 + timedelta(days=1),
        equity=1_010.0,
        deploy_equity=1_000.0,
        current_entry_ts=None,
        current_stop=None,
        current_take=None,
    )


class TestFormat(unittest.TestCase):
    def test_entry_has_price_stop_target_and_never_claims_an_order(self):
        subject, body = format_trade({
            "side": "buy", "strategy": "capitulation_dip_uni",
            "symbol": "UNI/USDT", "timeframe": "4h", "env": "paper",
            "ts": T0, "price": 4.8123, "qty": 12.5, "notional": 60.15,
            "entry": 4.8123, "stop": 4.7, "take_profit": 5.1,
            "reason": "capitulation",
            "wallet": 1_000.0, "deploy_equity": 1_000.0,
        })
        self.assertEqual(
            subject,
            "capitulation_dip_uni:buy:4.8123: 4.8123/4.7000/5.1000",
        )
        self.assertIn("TRADE OPENED", body)
        self.assertIn("entry     4.8123", body)
        self.assertIn("price     4.8123", body)
        self.assertIn("stop      4.7000", body)
        self.assertIn("target    5.1000", body)
        self.assertIn("wallet    $1,000.00 now", body)
        self.assertIn("no order was sent", body)

    def test_exit_reports_realised_pnl(self):
        subject, body = format_trade({
            "side": "sell", "strategy": "capitulation_dip_uni",
            "symbol": "UNI/USDT", "timeframe": "4h", "env": "paper",
            "ts": T0, "price": 5.0, "qty": 12.5, "notional": 62.5,
            "entry": 4.8123, "stop": 4.7, "take_profit": 5.1,
            "pnl": 12.34, "return_pct": 0.042, "held_hours": 72.5,
            "reason": "take profit (5.1000)", "wallet": 1_012.34,
        })
        self.assertEqual(
            subject,
            "capitulation_dip_uni:sell:5.0000: 4.8123/4.7000/5.1000",
        )
        self.assertIn("TRADE CLOSED", body)
        self.assertIn("entry     4.8123", body)
        self.assertIn("pnl       +$12.34  (+4.20%)", body)
        self.assertIn("held      72.5h", body)
        self.assertIn("take profit (5.1000)", body)


class TestCursor(unittest.TestCase):
    def test_first_cycle_only_anchors_history(self):
        fills = [
            Fill(T0, "buy", 10.0, 100.0, 0.0, "entry"),
            Fill(T0 + timedelta(days=2), "sell", 10.0, 110.0, 0.0, "target"),
        ]
        channel = Capture()
        state = notify({}, fills, channel)
        self.assertEqual(channel.calls, [])
        self.assertTrue(state["initialized"])
        self.assertEqual(state["last_fill"], _fill_key(fills[-1]))

    def test_new_fill_is_sent_once_and_duplicates_are_ignored(self):
        fills = [Fill(T0, "buy", 10.0, 100.0, 0.0, "entry")]
        channel = Capture()
        first = notify({}, fills, channel)
        self.assertEqual(channel.calls, [])
        new_fill = Fill(T0 + timedelta(days=1), "buy", 11.0, 101.0, 0.0, "entry")
        all_fills = fills + [new_fill]
        second = notify({"alerts": first}, all_fills, channel)
        self.assertEqual(len(channel.calls), 1)
        self.assertEqual(channel.calls[0]["side"], "buy")
        self.assertEqual(channel.calls[0]["price"], 101.0)
        self.assertEqual(second["last_fill"], _fill_key(new_fill))
        third = notify({"alerts": second}, all_fills, channel)
        self.assertEqual(len(channel.calls), 1)
        self.assertEqual(third["last_fill"], _fill_key(new_fill))

    def test_a_failed_delivery_keeps_the_cursor_for_retry(self):
        fills = [Fill(T0, "buy", 10.0, 100.0, 0.0, "entry")]
        channel = Capture(ok=False)
        first = notify({}, [], channel)
        second = notify({"alerts": first}, fills, channel)
        self.assertEqual(second["last_fill"], "")
        self.assertEqual(len(channel.calls), 1)
        third = notify({"alerts": second}, fills, channel)
        self.assertEqual(len(channel.calls), 2)

    def test_missing_cursor_reanchors_without_replaying_the_window(self):
        fills = [Fill(T0, "buy", 10.0, 100.0, 0.0, "entry")]
        channel = Capture()
        state = notify(
            {"alerts": {"initialized": True, "last_fill": "gone"}}, fills, channel
        )
        self.assertEqual(channel.calls, [])
        self.assertEqual(state["last_fill"], _fill_key(fills[-1]))

    def test_alerts_off_advances_the_cursor_silently(self):
        fills = [Fill(T0, "buy", 10.0, 100.0, 0.0, "entry")]
        state = notify({"alerts": {"initialized": True, "last_fill": ""}}, fills, None)
        self.assertEqual(state["last_fill"], _fill_key(fills[-1]))


class _Response:
    status = 200

    def __init__(self, payload=b'{"ok": true}'):
        self.payload = payload

    def read(self, _limit=-1):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ForwardSilenceTests(unittest.TestCase):
    def test_forward_unit_cannot_read_live_alert_channels(self):
        root = Path(__file__).resolve().parents[1]
        unit = root / "deploy" / "tradingbot-forward.service"
        text = unit.read_text(encoding="utf-8")
        self.assertIn("/etc/tradingbot/forward.env", text)
        self.assertNotIn("alerts.env", text)


class TestChannels(unittest.TestCase):
    def test_telegram_posts_json_and_hides_the_token_on_error(self):
        channel = TelegramChannel("123:SECRET", "42")
        with mock.patch("bot.notify.urllib.request.urlopen", return_value=_Response()) as open_:
            channel.send("subject", "body")
        request = open_.call_args.args[0]
        self.assertIn("123:SECRET", request.full_url)
        self.assertIn(b'"chat_id": "42"', request.data)
        self.assertIn(b"subject", request.data)

        error = urllib.error.HTTPError(
            "https://api.telegram.org/bot123:SECRET/sendMessage", 401, "no", None, None
        )
        with mock.patch("bot.notify.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                channel.send("subject", "body")
        self.assertNotIn("SECRET", str(caught.exception))

    def test_ntfy_sends_the_body_plainly(self):
        channel = NtfyChannel("https://ntfy.sh/topic", token="tok")
        with mock.patch("bot.notify.urllib.request.urlopen", return_value=_Response()) as open_:
            channel.send("subject", "body")
        request = open_.call_args.args[0]
        self.assertEqual(request.data, b"body")
        self.assertEqual(request.get_header("Authorization"), "Bearer tok")

    def test_email_builds_a_message_and_uses_starttls(self):
        sent = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                self.host, self.port, self.timeout = host, port, timeout
                self.tls = False
                self.credentials = None
                sent.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def starttls(self, context=None):
                self.tls = True

            def login(self, user, password):
                self.credentials = (user, password)

            def send_message(self, message):
                self.message = message

        channel = EmailChannel(
            "smtp.example", ["to@example.com"], port=2525,
            user="me@example.com", password="pw", sender="bot@example.com",
        )
        with mock.patch("bot.notify.smtplib.SMTP", FakeSMTP):
            channel.send("Subject", "Body")
        session = sent[0]
        self.assertEqual((session.host, session.port), ("smtp.example", 2525))
        self.assertTrue(session.tls)
        self.assertEqual(session.credentials, ("me@example.com", "pw"))
        self.assertIsInstance(session.message, EmailMessage)
        self.assertEqual(session.message["Subject"], "Subject")
        self.assertEqual(session.message["To"], "to@example.com")


class TestNotifier(unittest.TestCase):
    def test_one_broken_channel_does_not_block_the_other(self):
        logs = []

        class Bad:
            name = "bad"

            def send(self, subject, body):
                raise RuntimeError("down")

        class Good:
            name = "good"

            def __init__(self):
                self.calls = []

            def send(self, subject, body):
                self.calls.append((subject, body))

        good = Good()
        notifier = Notifier([Bad(), good], log=logs.append)
        self.assertTrue(notifier.send("s", "b"))
        self.assertEqual(good.calls, [("s", "b")])
        self.assertIn("bad failed", logs[0])

    def test_from_env_enables_every_complete_channel(self):
        notifier = Notifier.from_env({
            "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1",
            "NTFY_URL": "https://ntfy.sh/topic",
            "SMTP_HOST": "smtp.example", "ALERT_EMAIL_TO": "a@b,c@d",
            "SMTP_FROM": "from@example.com", "SMTP_PORT": "2525",
        }, log=lambda _m: None)
        self.assertEqual(notifier.describe(), "telegram, ntfy, email")

    def test_email_is_not_enabled_without_a_password_for_its_user(self):
        logs = []
        notifier = Notifier.from_env({
            "SMTP_HOST": "smtp.gmail.com", "ALERT_EMAIL_TO": "a@b",
            "SMTP_FROM": "a@b", "SMTP_USER": "a@b",
        }, log=logs.append)
        self.assertFalse(notifier.enabled)
        self.assertTrue(any("email ignored" in line for line in logs))

    def test_from_env_warns_about_an_incomplete_group_without_leaking_secrets(self):
        logs = []
        notifier = Notifier.from_env(
            {"SMTP_HOST": "smtp.example", "SMTP_PASSWORD": "hunter2"},
            log=logs.append,
        )
        self.assertFalse(notifier.enabled)
        self.assertTrue(any("email ignored" in line for line in logs))
        self.assertFalse(any("hunter2" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
