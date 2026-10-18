"""Small in-memory limits: per-visitor rate limits and caps on concurrent work."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager


class RateLimiter:
    """Sliding-window limit of ``limit`` hits per ``window_seconds`` for each key."""

    PRUNE_EVERY = 1000  # hits between sweeps of visitors who have gone quiet

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._since_prune = 0

    def check(self, key: str) -> float:
        """Record a hit. Returns 0 if allowed, otherwise seconds until the next free slot."""
        now = time.monotonic()
        with self._lock:
            self._since_prune += 1
            if self._since_prune >= self.PRUNE_EVERY:
                self._since_prune = 0
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    del self._hits[k]
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, self.window - (now - hits[0]))
            hits.append(now)
            return 0.0

    def __len__(self) -> int:
        return len(self._hits)


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
