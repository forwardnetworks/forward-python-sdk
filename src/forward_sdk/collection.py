"""The outcome of a network collection."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CollectionRun"]


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """A collection that was started or joined, and what became of it.

    Attributes:
        task_id: The collector task: the one started, or the one joined.
        status: The task's last known status: ``SUCCEEDED`` once it finished. When
            the call did not wait, ``QUEUED`` for a task it started, and the status
            Forward listed for a task it joined.
        snapshot_id: The snapshot the collection produced, or ``None`` when the
            call did not wait. It may still be processing; follow with
            ``client.snapshots.wait_until_processed()``.
    """

    task_id: str
    status: str
    snapshot_id: str | None = None
