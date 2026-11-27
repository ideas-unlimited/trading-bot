"""Offline tests for the heartbeat pinger."""

import unittest

from bot.heartbeat import Heartbeat


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class HeartbeatTests(unittest.TestCase):
    def test_disabled_never_pings(self) -> None:
        calls: list[str] = []
        beat = Heartbeat(None, transport=lambda url, t: calls.append(url))
        self.assertFalse(beat.enabled)
        self.assertFalse(beat.ping(force=True))
        self.assertEqual(calls, [])

    def test_throttles_until_the_window_elapses(self) -> None:
        clock = _Clock()
        calls: list[str] = []
        beat = Heartbeat("https://hc.example/abc", every=60, clock=clock,
                         transport=lambda url, t: calls.append(url))
        self.assertTrue(beat.ping())
        clock.now += 30
        self.assertFalse(beat.ping())
        clock.now += 31
        self.assertTrue(beat.ping())
        self.assertEqual(len(calls), 2)
        self.assertEqual(beat.sent, 2)

    def test_force_bypasses_the_throttle(self) -> None:
        calls: list[str] = []
        beat = Heartbeat("https://hc.example/abc", every=3600,
                         transport=lambda url, t: calls.append(url))
        self.assertTrue(beat.ping(force=True))
        self.assertTrue(beat.ping(force=True))
        self.assertEqual(len(calls), 2)

    def test_transport_failure_is_swallowed_and_counted(self) -> None:
        clock = _Clock()

        def boom(url: str, timeout: float) -> None:
            raise OSError("network down")

        beat = Heartbeat("https://hc.example/abc", every=60, clock=clock, transport=boom)
        self.assertFalse(beat.ping())
        self.assertEqual(beat.failures, 1)
        self.assertEqual(beat.sent, 0)
        # A failed attempt still counts as the window, so a down service is
        # not retried on every poll.
        clock.now += 30
        self.assertFalse(beat.ping())
        self.assertEqual(beat.failures, 1)
        clock.now += 31
        self.assertFalse(beat.ping())
        self.assertEqual(beat.failures, 2)

    def test_blank_url_is_disabled(self) -> None:
        beat = Heartbeat("   ")
        self.assertFalse(beat.enabled)
        self.assertFalse(beat.ping())


if __name__ == "__main__":
    unittest.main()
