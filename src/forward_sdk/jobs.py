"""What cancelling a snapshot's jobs did."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobCancellation:
    """The outcome of ``snapshots.cancel_jobs()``, counted in rows of Forward's job list.

    A row is one job, or one group of identical jobs (Forward groups them by type,
    network and snapshot).

    Attributes:
        matched: Rows of active work that belong to the snapshot.
        canceled: Rows Forward accepted a cancel for. Cancelling is cooperative: the
            work stops when it next checks, so read the active jobs again to confirm.
        already_done: Single-job rows that had finished before the cancel arrived.
    """

    matched: int
    canceled: int
    already_done: int
