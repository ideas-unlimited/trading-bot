"""Dead-man's switch: tell an outside service the bot is alive.

A process cannot report its own death.  A crashed loop, a full disk or a
powered-off box all look exactly like a quiet market -- no signals, no output,
no orders.  So each cycle pings a URL that an outside service expects on a
fixed schedule; when the pings stop, that service raises the alarm.

Works with healthchecks.io, Uptime Kuma push monitors, Cronitor, or any HTTP
endpoint that accepts a GET.  Nothing is sent beyond the request itself, so the
URL is the only secret and it belongs in the bot's env file, not in git.
"""

from __future__ import annotations

import time
import urllib.request
from typing import Callable

# Default: ping every 30 minutes.  Pair it with an hourly check and a 2-hour
# grace period on the service: one missed ping is noise, two is an outage.
DEFAULT_EVERY = 1800.0


class Heartbeat:
    """Throttled HTTP ping.  Never raises: a monitoring outage must not stop
    the trading loop, and a failed ping is itself only a warning."""

    def __init__(
        self,
        url: str | None,
        *,
        every: float = DEFAULT_EVERY,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        transport: Callable[[str, float], None] | None = None,
    ) -> None:
        self.url = (url or "").strip() or None
        self.every = max(1.0, float(every))
        self.timeout = float(timeout)
        self._clock = clock
        self._transport = transport or _http_get
        self._last = float("-inf")
        self.sent = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def ping(self, *, force: bool = False) -> bool:
        """Send a ping if the throttle window has elapsed.  Returns True when
        a request was actually made and accepted."""
        if self.url is None:
            return False
        now = self._clock()
        if not force and (now - self._last) < self.every:
            return False
        # Book the attempt before sending: a service that times out must not
        # be re-hit on every 60-second poll.
        self._last = now
        try:
            self._transport(self.url, self.timeout)
        except Exception:
            self.failures += 1
            return False
        self.sent += 1
        return True


def _http_get(url: str, timeout: float) -> None:
    """Fire-and-forget GET.  Any 2xx/3xx satisfies urllib; anything else
    raises, which the caller counts as a failed ping."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        response.read(64)
