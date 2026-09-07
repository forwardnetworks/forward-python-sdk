"""Per-execution records for NQE queries.

A sync typically runs many queries, fanned across a thread pool, and afterwards
wants to say what happened: how long Forward spent, how many rows came back, how
often it was polled. The individual handles are long gone by then, so the client
retains a record of each.

Reading these must never fail. The place they get read is a failure path, while
something is already being recorded as having gone wrong, and telemetry that
raises there would replace a useful error with a useless one.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["ExecutionReport", "ExecutionReports"]

#: Default number of records kept. A long-lived client would otherwise grow
#: without limit; a per-run client never reaches this.
DEFAULT_RETENTION = 1000


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """What happened to one NQE execution.

    Attributes:
        execution_key: Forward's identifier for the execution.
        network_id: The network it ran against.
        snapshot_id: The snapshot it ran against, when Forward reported one.
        query: How the query was referenced, for attribution.
        millis_executing: Time Forward reports it spent, in milliseconds.
        rows_produced: Rows Forward reports the query produced.
        poll_count: How many times the SDK asked for status.
        poll_sleep_seconds: Total time spent waiting between polls.
        terminal_reason: How it ended: the outcome Forward reported, or the
            client-side reason it stopped waiting.
        wall_seconds: Time from starting the execution to it finishing.
    """

    execution_key: str
    network_id: str
    snapshot_id: str | None = None
    query: str | None = None
    millis_executing: int | None = None
    rows_produced: int | None = None
    poll_count: int = 0
    poll_sleep_seconds: float = 0.0
    terminal_reason: str | None = None
    wall_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """A plain dictionary, for logging or persisting alongside a job result."""
        return asdict(self)


class ExecutionReports:
    """Thread-safe, bounded retention of :class:`ExecutionReport`.

    Appended to from whatever thread ran the query, so every operation takes a
    lock. Bounded because a client that lives for days would otherwise
    accumulate a record per query forever.
    """

    __slots__ = ("_lock", "_records")

    def __init__(self, maxlen: int | None = DEFAULT_RETENTION) -> None:
        self._lock = threading.Lock()
        self._records: deque[ExecutionReport] = deque(maxlen=maxlen or None)

    def record(self, report: ExecutionReport) -> None:
        with self._lock:
            self._records.append(report)

    def all(self) -> list[ExecutionReport]:
        """Every retained record, oldest first."""
        with self._lock:
            return list(self._records)

    def as_dicts(self) -> list[dict[str, Any]]:
        """Every retained record as plain dictionaries."""
        return [report.as_dict() for report in self.all()]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __bool__(self) -> bool:
        return len(self) > 0
