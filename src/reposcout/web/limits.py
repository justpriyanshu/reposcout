"""Per-visitor rate limits and caps on concurrent work.

Rate limits use the ``limits`` library (the engine behind slowapi) with a moving window.
Counters live in memory by default, which is right for a single server; point
``REPOSCOUT_RATE_LIMIT_STORAGE`` at ``redis://...`` (with the ``redis`` package installed)
to share them between several processes or servers.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from limits import RateLimitItemPerSecond, storage, strategies


class RateLimiter:
    """Moving-window limit of ``limit`` hits per ``window_seconds`` for each key."""

    def __init__(self, limit: int, window_seconds: float, storage_uri: str = "memory://", name: str = ""):
        self.limit = limit
        self.window = window_seconds
        self.item = RateLimitItemPerSecond(limit, max(1, int(window_seconds)))
        self.storage = storage.storage_from_string(storage_uri)
        self.strategy = strategies.MovingWindowRateLimiter(self.storage)
        self.namespace = f"reposcout:{name or 'limit'}"

    def check(self, key: str) -> float:
        """Record a hit. Returns 0 if allowed, otherwise seconds until the next free slot."""
        if self.strategy.hit(self.item, self.namespace, key):
            return 0.0
        reset, _remaining = self.strategy.get_window_stats(self.item, self.namespace, key)
        return max(1.0, reset - time.time())


class Busy(Exception):
    """No free slot right now."""


class ConcurrencyLimit:
    """At most ``total`` requests at once overall, and ``per_key`` per visitor.

    It never waits: a request that finds no free slot is turned away immediately, so a
    burst of slow requests can't tie up every server thread."""

    def __init__(self, total: int, per_key: int = 1):
        self.total = max(1, total)
        self.per_key = max(1, per_key)
        self._running = 0
        self._by_key: dict[str, int] = {}
        self._lock = threading.Lock()

    @contextmanager
    def slot(self, key: str):
        with self._lock:
            if self._running >= self.total:
                raise Busy("server")
            if self._by_key.get(key, 0) >= self.per_key:
                raise Busy("visitor")
            self._running += 1
            self._by_key[key] = self._by_key.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._running -= 1
                left = self._by_key[key] - 1
                if left:
                    self._by_key[key] = left
                else:
                    del self._by_key[key]
