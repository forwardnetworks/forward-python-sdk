"""The outcome of a network collection."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CollectionRun"]


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """A collection that was started, and what became of it.

    Attributes:
        task_id: The collector task Forward created.
        status: The task's last known status: ``SUCCEEDED`` once it finished, or
            ``QUEUED`` when the call did not wait.
        snapshot_id: The snapshot the collection produced, or ``None`` when the
            call did not wait. It may still be processing; follow with
            ``client.snapshots.wait_until_processed()``.
    """

    task_id: str
    status: str
    snapshot_id: str | None = None
