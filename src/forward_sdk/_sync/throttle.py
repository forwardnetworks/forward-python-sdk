# Generated from src/forward_sdk/_async/throttle.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Client-side request pacing."""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

__all__ = ["RateLimiter", "Throttle"]


@runtime_checkable
class Throttle(Protocol):
    """Paces outgoing requests.

    Implement this to coordinate across processes. The SDK's own limiter is
    per-client, which is enough for one program, but a fleet of workers sharing
    one Forward account needs a shared counter (a cache or a Redis token bucket)
    to stay under the account's ceiling.
    """

    def acquire(self) -> float:
        """Block until another request may be sent; return seconds slept."""


class RateLimiter:
    """Spaces requests by a minimum interval.

    Timed from the completion of the previous acquisition, so a burst cannot
    accumulate credit while the client is idle.
    """

    __slots__ = ("_interval", "_lock", "_next_allowed")

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._interval = 60.0 / requests_per_minute
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> float:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            else:
                wait = 0.0
            self._next_allowed = now + self._interval
            return wait
