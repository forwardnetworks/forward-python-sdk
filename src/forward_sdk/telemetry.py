"""Request counters and lifecycle hooks.

The SDK is often driven by long-running sync jobs where the interesting
questions are operational: how close is this to the server's rate limit, how
much time went to backoff, how many NQE pages were fetched. Counters answer
those without the caller instrumenting every call site.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import httpx

    from forward_sdk._generated._defs import OpDef

__all__ = ["CounterSnapshot", "Counters", "Hooks"]


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    """An immutable reading of a client's counters."""

    http_attempts: int = 0
    http_retries: int = 0
    http_errors: int = 0
    http_429: int = 0
    transport_errors: int = 0
    throttle_sleep_seconds: float = 0.0
    retry_sleep_seconds: float = 0.0
    nqe_runs: int = 0
    nqe_executions: int = 0
    nqe_polls: int = 0
    nqe_pages: int = 0
    nqe_rows: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    #: When the first and most recent requests were sent, as Unix timestamps.
    #: Without the window they span, a request rate cannot be computed, which
    #: is what a release gate against a rate limit needs.
    first_attempt_at: float = 0.0
    last_attempt_at: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        """Time between the first and most recent request."""
        if not self.first_attempt_at or not self.last_attempt_at:
            return 0.0
        return max(0.0, self.last_attempt_at - self.first_attempt_at)

    @property
    def attempts_per_minute(self) -> float:
        """Observed request rate, for comparison against a server-side limit.

        Zero until at least two requests have been sent far enough apart to
        span a measurable window.
        """
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.http_attempts * 60.0 / elapsed

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class Counters:
    """Thread-safe request counters.

    A single client is commonly shared across a thread pool, so every update is
    taken under a lock and :meth:`snapshot` returns a consistent reading.
    """

    __slots__ = ("_lock", "_values")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] = {f.name: 0 for f in fields(CounterSnapshot)}

    def increment(self, name: str, amount: float = 1) -> None:
        with self._lock:
            if name not in self._values:
                raise KeyError(f"unknown counter {name!r}")
            self._values[name] += amount

    def mark_attempt(self, when: float) -> None:
        """Record when a request was sent, for the observed-rate window."""
        with self._lock:
            self._values["http_attempts"] += 1
            if not self._values["first_attempt_at"]:
                self._values["first_attempt_at"] = when
            self._values["last_attempt_at"] = when

    def snapshot(self) -> CounterSnapshot:
        with self._lock:
            values = dict(self._values)
        # Durations stay floats; every other counter is a whole number of events.
        coerced: dict[str, Any] = {
            key: (float(value) if key.endswith(("_seconds", "_at")) else int(value))
            for key, value in values.items()
        }
        return CounterSnapshot(**coerced)

    def reset(self) -> None:
        with self._lock:
            for key in self._values:
                self._values[key] = 0


class Hooks:
    """Callbacks invoked around each request.

    Subclass and override only what you need; every method defaults to doing
    nothing. Hooks run inline on the calling thread, so they should be cheap.
    A hook that raises is logged and ignored rather than failing the request it
    was observing.
    """

    def on_request(self, operation: OpDef | None, request: httpx.Request) -> None:
        """Called immediately before a request is sent, once per attempt."""

    def on_response(
        self, operation: OpDef | None, response: httpx.Response, elapsed: float
    ) -> None:
        """Called after a response is received, including error responses."""

    def on_retry(self, operation: OpDef | None, attempt: int, delay: float, reason: str) -> None:
        """Called before sleeping between retries."""

    def on_sleep(self, seconds: float, reason: str) -> None:
        """Called before any client-side pause, including rate-limit pacing."""
