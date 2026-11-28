"""Offline tests for the book watchdog (no Postgres, no network)."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.watchdog import (
    alert_message,
    format_age,
    load_state,
    run_once,
    save_state,
    stale_bots,
)

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    """Just enough connection for running_bots()."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql
        return _Rows(self.rows)


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return True

    def describe(self):
        return "test"


class _Heartbeat:
    enabled = True

    def __init__(self):
        self.pings = 0

    def ping(self, force=False):
        self.pings += 1
        return True


def bot(bot_id=1, *, minutes_ago=None, halted=False, symbol="UNI/USDT"):
    beat = None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago)
    return {
        "id": bot_id,
        "name": f"bot-{bot_id}",
        "symbol": symbol,
        "timeframe": "4h",
        "halted": halted,
        "started_at": NOW - timedelta(days=2),
        "beat": beat,
        "last_heartbeat_at": beat,
    }


class FormatTests(unittest.TestCase):
    def test_ages_read_like_a_person_wrote_them(self):
        self.assertEqual(format_age(42), "42s")
        self.assertEqual(format_age(14 * 60), "14m")
        self.assertEqual(format_age(3 * 3600 + 4 * 60), "3h 04m")
        self.assertEqual(format_age(2 * 86400 + 3 * 3600), "2d 03h")
        self.assertEqual(format_age(None), "never")


class StaleTests(unittest.TestCase):
    def test_only_silent_bots_are_stale(self):
        bots = [bot(1, minutes_ago=1), bot(2, minutes_ago=40)]
        out = stale_bots(bots, NOW, timedelta(minutes=10))
        self.assertEqual([item["id"] for item in out], [2])
        self.assertAlmostEqual(out[0]["age_s"], 40 * 60, delta=1)

    def test_a_bot_that_never_beat_is_stale(self):
        out = stale_bots([bot(1)], NOW, timedelta(minutes=10))
        self.assertEqual([item["id"] for item in out], [1])


class StateTests(unittest.TestCase):
    def test_a_corrupt_file_starts_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchdog.json"
            path.write_text("not json", "utf-8")
            self.assertEqual(load_state(path)["bots"], {})

    def test_round_trip_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "watchdog.json"
            save_state(path, {"version": 1, "bots": {"7": {"name": "x"}}})
            self.assertEqual(load_state(path)["bots"]["7"]["name"], "x")
            self.assertFalse((path.parent / (path.name + ".tmp")).exists())


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "watchdog.json"
        self.notifier = _Notifier()
        self.heartbeat = _Heartbeat()

    def run_at(self, rows, now=NOW, **kwargs):
        return run_once(
            _Conn(rows),
            state_path=self.state,
            now=now,
            heartbeat=self.heartbeat,
            notifier=self.notifier,
            log=lambda _line: None,
            **kwargs,
        )

    def test_a_dead_loop_is_named_and_the_ping_is_withheld(self):
        summary = self.run_at([bot(1, minutes_ago=1), bot(2, minutes_ago=40)])
        self.assertEqual(summary["running"], 2)
        self.assertEqual([item["id"] for item in summary["stale"]], [2])
        self.assertEqual(len(self.notifier.sent), 1)
        subject, body = self.notifier.sent[0]
        self.assertIn("bot-2", subject)
        self.assertIn("NO HEARTBEAT", body)
        self.assertIn("40m", body)
        self.assertFalse(summary["pinged"])
        self.assertEqual(self.heartbeat.pings, 0)

    def test_one_episode_alerts_once_then_repeats_later(self):
        rows = [bot(1, minutes_ago=40)]
        self.run_at(rows)
        summary = self.run_at(rows, now=NOW + timedelta(hours=1))
        self.assertEqual(summary["alerted"], 0)
        self.assertEqual(len(self.notifier.sent), 1)
        summary = self.run_at(rows, now=NOW + timedelta(hours=7))
        self.assertEqual(summary["alerted"], 1)
        self.assertEqual(len(self.notifier.sent), 2)

    def test_a_flat_book_still_pings_the_external_check(self):
        summary = self.run_at([])
        self.assertEqual(summary["running"], 0)
        self.assertTrue(summary["pinged"])
        self.assertEqual(self.notifier.sent, [])

    def test_recovery_clears_the_episode_and_resumes_pinging(self):
        self.run_at([bot(1, minutes_ago=40)])
        # Still the same bot object times, judged 5 minutes later: it is fresh.
        summary = self.run_at([bot(1, minutes_ago=1)], now=NOW + timedelta(minutes=5))
        self.assertTrue(summary["pinged"])
        self.assertEqual(summary["recovered"], 1)
        subjects = [subject for subject, _body in self.notifier.sent]
        self.assertIn("BOT RECOVERED: bot-1", subjects)
        self.assertEqual(load_state(self.state)["bots"], {})

    def test_a_bot_that_stops_is_cleared_without_a_recovery_message(self):
        self.run_at([bot(1, minutes_ago=40)])
        summary = self.run_at([], now=NOW + timedelta(minutes=15))
        self.assertEqual(summary["recovered"], 0)
        self.assertTrue(summary["pinged"])
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(load_state(self.state)["bots"], {})

    def test_dry_run_records_nothing(self):
        self.run_at([bot(1, minutes_ago=40)], persist=False)
        self.assertFalse(self.state.exists())
        self.run_at([bot(1, minutes_ago=40)], persist=False)
        self.assertEqual(len(self.notifier.sent), 2)

    def test_alert_body_never_leaks_a_secret(self):
        subject, body = alert_message(bot(1, minutes_ago=40), NOW)
        self.assertNotIn("hc-ping", body)
        self.assertNotIn("https://", body)


if __name__ == "__main__":
    unittest.main()
