"""Per-IP login rate limit and client address resolution.

The limiter is in memory: it resets when the process restarts and is not shared between workers.
That is acceptable for one API process behind one proxy, because the account lockout (which is in
the database) is the durable defence; this layer only slows one address down.
"""

import math
from collections import deque
from collections.abc import Mapping
from datetime import datetime
from ipaddress import ip_address

from services.api.clock import Clock


class SlidingWindowLimiter:
    def __init__(
        self, max_events: int, window_seconds: int, clock: Clock, max_keys: int = 10_000
    ) -> None:
        self._max = max_events
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque[datetime]] = {}

    def hit(self, key: str) -> int:
        """Record an event. Returns 0 if allowed, else the seconds until it would be."""
        now = self._clock()
        events = self._events.setdefault(key, deque())
        while events and (now - events[0]).total_seconds() >= self._window:
            events.popleft()
        if len(events) >= self._max:
            return math.ceil(self._window - (now - events[0]).total_seconds())
        events.append(now)
        self._prune(now)
        return 0

    def _prune(self, now: datetime) -> None:
        if len(self._events) <= self._max_keys:
            return
        for key in [
            k
            for k, v in self._events.items()
            if not v or (now - v[-1]).total_seconds() >= self._window
        ]:
            del self._events[key]
        while len(self._events) > self._max_keys:
            del self._events[next(iter(self._events))]  # oldest key first


def client_ip(headers: Mapping[str, str], peer: str | None, trusted_hops: int) -> str:
    """The caller's address.

    With no trusted proxy, only the socket peer is believable: X-Forwarded-For is whatever the
    caller wrote. With N trusted proxies, the Nth entry from the right is the address the nearest
    trusted proxy saw; entries to its left are caller-supplied and ignored.
    """
    if trusted_hops > 0:
        parts = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            candidate = parts[-trusted_hops]
            try:
                return str(ip_address(candidate))
            except ValueError:
                pass
    return peer or "unknown"
