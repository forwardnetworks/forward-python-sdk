# Generated from src/forward_sdk/_async/services/snapshots.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Snapshots: the point-in-time models Forward builds from collected data.

Most reads are against a snapshot. Passing ``snapshot_id=None`` anywhere in the
SDK means "the network's latest processed snapshot", which Forward implements by
omitting the parameter, so no extra request is needed to resolve it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from forward_sdk._generated.models import SnapshotInfo, SnapshotState
from forward_sdk._ops import core as ops
from forward_sdk._ops import nqe_repo as repo_ops
from forward_sdk._sync.services._base import Service
from forward_sdk._sync.services.nqe import NqeService
from forward_sdk.errors import (
    ForwardExecutionError,
    ForwardNotFoundError,
    ForwardTimeoutError,
)
from forward_sdk.nqe.where import tag_scope

__all__ = ["SnapshotsService"]

PROCESSED = "PROCESSED"

#: States a snapshot never leaves. Anything else is still in flight.
TERMINAL_STATES = frozenset(
    {PROCESSED, "FAILED", "CANCELED", "TIMED_OUT", "ARCHIVED", "RESTORE_FAILED"}
)
FAILED_STATES = TERMINAL_STATES - {PROCESSED, "ARCHIVED"}

DEFAULT_POLL_INTERVAL = 15.0

#: Reachability job states, which Forward has reported under several names.
REACHABILITY_DONE = frozenset({"COMPLETED", "DONE", "READY", "SUCCESS"})
REACHABILITY_FAILED = frozenset({"FAILED", "ERROR", "CANCELED"})

#: Probe used to find a snapshot that actually collected devices, not merely one
#: that finished processing.
COLLECTED_PROBE = """
foreach device in network.devices
where device.snapshotInfo.result == DeviceSnapshotResult.completed
where device.platform.vendor != Vendor.FORWARD_CUSTOM
{scope}
select {{ name: device.name }}
"""


#: Reuse a resolved snapshot for as long as the client lives.
LIFETIME = "lifetime"


class _Miss:
    """Sentinel for "not cached", distinct from a cached ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<cache miss>"


_MISS = _Miss()


#: Forward's trigger for a snapshot it created to analyse a change set. Absent
#: from the published description, which lists every other trigger, so it
#: arrives as an unknown enum value and is compared as a string.
PREDICT_TRIGGER = "PREDICT"


def is_predicted(snapshot: SnapshotInfo) -> bool:
    """Whether Forward made this snapshot to analyse a change set."""
    trigger = getattr(snapshot, "processing_trigger", None)
    if trigger is None:
        return False
    # Exact match on the value. A suffix test would be wrong for the same
    # reason "UNPROCESSED" ends with "PROCESSED".
    value = getattr(trigger, "value", trigger)
    return str(value).upper() == PREDICT_TRIGGER


def _state_of(snapshot: SnapshotInfo) -> str:
    return str(snapshot.state) if snapshot.state is not None else ""


class SnapshotsService(Service):
    """List, inspect, upload and wait on snapshots."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self._nqe_service: NqeService | None = None
        self._resolved: dict[tuple[Any, ...], tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        """Forget any cached snapshot resolution.

        Call this when something you did makes "latest" mean something new and
        the SDK cannot know: an upload from another process, or a snapshot that
        finished processing while this client was running. Uploading through
        this client clears it for you.
        """
        self._resolved.clear()

    def _caching(self) -> bool:
        ttl = self._config.snapshot_cache_ttl
        return bool(ttl == LIFETIME or ttl > 0)

    def _cached(self, key: tuple[Any, ...]) -> Any:
        """A previously resolved answer to exactly this question, or a miss.

        Returns ``_MISS`` rather than ``None`` because ``None`` is a real
        answer: a network with no processed snapshot resolves to nothing, and
        re-asking every time would spend the budget the cache exists to save.

        ``"lifetime"`` never expires. That is the point of it: a run holding one
        point in time must not have the answer change underneath it, and a TTL
        expiring mid-run would let the next resolution return a different
        snapshot, straddling two moments with nothing raising.

        A race between two threads costs a duplicate resolution, never a wrong
        answer, since both compute the same thing from the same server state.
        """
        ttl = self._config.snapshot_cache_ttl
        if not self._caching():
            return _MISS
        entry = self._resolved.get(key)
        if entry is None:
            self._transport.counters.increment("cache_misses")
            return _MISS
        fetched, value = entry
        if ttl != LIFETIME and (time.monotonic() - fetched) >= ttl:
            self._transport.counters.increment("cache_misses")
            return _MISS
        self._transport.counters.increment("cache_hits")
        return value

    def _remember(self, key: tuple[Any, ...], value: Any) -> Any:
        if self._caching():
            self._resolved[key] = (time.monotonic(), value)
        return value

    def list(
        self,
        network_id: str | None = None,
        *,
        state: str | SnapshotState | Sequence[str] | None = None,
        limit: int | None = None,
        include_archived: bool = False,
        **filters: Any,
    ) -> list[SnapshotInfo]:
        """List a network's snapshots, newest first."""
        payload = self._send_json(
            ops.list_snapshots(
                network_id=self._network(network_id),
                state=_state_param(state),
                limit=limit,
                # Sent even when false. Forward's default happens to match, but
                # relying on a server default means this argument does not
                # actually assert anything, and a change upstream would alter
                # behaviour silently.
                include_archived=bool(include_archived),
                **filters,
            )
        )
        rows = (payload or {}).get("snapshots") or []
        return [SnapshotInfo.model_validate(row) for row in rows]

    def latest_processed(
        self, network_id: str | None = None, *, include_predicted: bool = False
    ) -> SnapshotInfo | None:
        """The most recent fully processed snapshot, or ``None`` if there is none.

        Uses the snapshot listing rather than Forward's dedicated endpoint,
        which it deprecated.

        The listing's order is not documented, so this does not take the first
        row of a one-row request. It sorts what comes back by when each snapshot
        finished processing. Picking an arbitrary processed snapshot would pin a
        sync to the wrong baseline without anything appearing to go wrong.

        Predicted snapshots are excluded by default. Forward creates one for
        every Predict run, and they are processed like any other, so on a
        network using Predict the newest processed snapshot is very often a
        prediction rather than a state the network was ever in. Basing a change
        set on one predicts a change against a change.

        The filter excludes ``PREDICT`` rather than requiring ``COLLECTION``,
        and the difference matters. A reprocessed snapshot reports
        ``REPROCESS`` and is real collected data; reprocessing is how a changed
        query or feature flag is picked up. Requiring ``COLLECTION`` would skip
        it and silently select the previous collection, which during a
        rehearsal is the snapshot taken while the change was still applied.

        Args:
            include_predicted: Consider predicted snapshots too. Forward does
                not publish ``PREDICT`` in its description, so a deployment not
                using Predict is unaffected either way.
        """
        key = ("latest_processed", self._network(network_id), include_predicted)
        cached = self._cached(key)
        if cached is not _MISS:
            return cached  # type: ignore[no-any-return]
        snapshots = self.list(network_id, state=PROCESSED)
        if not include_predicted:
            snapshots = [s for s in snapshots if not is_predicted(s)]
        latest = max(snapshots, key=_processed_order, default=None)
        return self._remember(key, latest)  # type: ignore[no-any-return]

    def latest_processed_id(
        self, network_id: str | None = None, *, include_predicted: bool = False
    ) -> str | None:
        snapshot = self.latest_processed(network_id, include_predicted=include_predicted)
        return snapshot.id if snapshot else None

    def latest_collected_id(
        self,
        network_id: str | None = None,
        *,
        scan_limit: int = 10,
        include_tags: Sequence[str] = (),
        exclude_tags: Sequence[str] = (),
        include_match: str = "any",
        where: str = "",
    ) -> str:
        """Find the newest snapshot that actually contains collected devices.

        A snapshot can be processed and still hold nothing useful, for instance
        when collection failed for every device in scope. Callers that need real
        data (a sync job, say) want this rather than simply the latest processed
        snapshot. Each candidate is probed with a one-row query.

        Args:
            scan_limit: How many recent snapshots to probe before giving up.
            include_tags: Only count devices carrying one of these tags.
            exclude_tags: Ignore devices carrying any of these tags.
            include_match: Whether one included tag is enough, or all are needed.
            where: Extra NQE ``where`` lines narrowing the probe. Use this when
                your scope is not expressible as tags -- a vendor or model
                allowlist, say -- so the probe asks about the devices you
                actually sync rather than the whole network. Build clauses with
                :mod:`forward_sdk.nqe.where` so values are escaped.

        Raises:
            ForwardNotFoundError: If no scanned snapshot has devices in scope.
        """
        resolved = self._network(network_id)
        key = (
            "latest_collected",
            resolved,
            scan_limit,
            tuple(include_tags),
            tuple(exclude_tags),
            include_match,
            where,
        )
        cached = self._cached(key)
        if cached is not _MISS:
            return cached  # type: ignore[no-any-return]
        scope = "\n".join(
            part
            for part in (
                tag_scope(
                    include=include_tags,
                    exclude=exclude_tags,
                    include_match=include_match,  # type: ignore[arg-type]
                ),
                where.strip(),
            )
            if part
        )
        probe = COLLECTED_PROBE.format(scope=scope)

        candidates = self.list(resolved, state=PROCESSED, limit=scan_limit)
        for snapshot in candidates:
            if not snapshot.id:
                continue
            result = self._nqe().run(probe, network_id=resolved, snapshot_id=snapshot.id, limit=1)
            if result.items:
                return self._remember(key, snapshot.id)  # type: ignore[no-any-return]

        raise ForwardNotFoundError(
            f"none of the {len(candidates)} most recent processed snapshots of network "
            f"{resolved} contain devices in scope",
            status=404,
        )

    def metrics(self, snapshot_id: str) -> Mapping[str, Any]:
        """Collection and processing counts for a snapshot."""
        return dict(self._send_json(ops.snapshot_metrics(snapshot_id=snapshot_id)) or {})

    def is_complete(self, snapshot_id: str) -> bool:
        """Whether a snapshot collected and processed everything without failures."""
        metrics = self.metrics(snapshot_id)
        return not any(
            int(metrics.get(key) or 0)
            for key in (
                "numCollectionFailureDevices",
                "numProcessingFailureDevices",
                "numCollectionFailureEndpoints",
                "numProcessingFailureEndpoints",
            )
        )

    def delete(self, snapshot_id: str) -> None:
        self._send_json(ops.delete_snapshot(snapshot_id=snapshot_id))

    def upload(
        self,
        files: str | Path | Sequence[str | Path],
        *,
        network_id: str | None = None,
        note: str | None = None,
        process: bool = True,
        wait: bool = False,
    ) -> SnapshotInfo:
        """Upload snapshot archives into a network.

        Args:
            files: One or more ``.zip`` archives. Several are merged into a
                single snapshot, so they must not describe the same device.
            process: Build the network model after importing.
            wait: Hold the request open until the import finishes. By default
                Forward answers as soon as the upload lands and continues in the
                background; poll with :meth:`wait_until_processed`.
        """
        paths = [Path(files)] if isinstance(files, (str, Path)) else [Path(f) for f in files]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"snapshot archive(s) not found: {', '.join(missing)}")

        payload = self._send_json(
            ops.upload_snapshot(
                network_id=self._network(network_id),
                files=paths,
                note=note,
                process=process,
                wait=wait,
            )
        )
        # A new snapshot is exactly the event that makes a cached "latest"
        # wrong, and this client caused it, so it does not wait for a TTL.
        self.clear_cache()
        return SnapshotInfo.model_validate(payload or {})

    def export(self, snapshot_id: str, *, only: str | None = None) -> Iterator[bytes]:
        """Download a snapshot archive in chunks.

        Args:
            only: Restrict the export, for example to ``CONFIG``.
        """
        spec = ops.export_snapshot(snapshot_id=snapshot_id, only=only)
        with self._transport.stream(spec) as response:
            for chunk in response.iter_bytes():
                yield chunk

    def download(self, snapshot_id: str, destination: str | Path, **kwargs: Any) -> Path:
        """Download a snapshot archive to a file."""
        path = Path(destination)
        with path.open("wb") as handle:
            for chunk in self.export(snapshot_id, **kwargs):
                handle.write(chunk)
        return path

    def wait_until_processed(
        self,
        snapshot_id: str,
        *,
        network_id: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float | None = 3600.0,
    ) -> SnapshotInfo:
        """Poll until a snapshot finishes processing.

        Forward has no endpoint for a single snapshot, so this watches the
        network's snapshot listing.

        Raises:
            ForwardExecutionError: Processing ended in a failure state.
            ForwardTimeoutError: ``timeout`` elapsed. Processing continues.
            ForwardNotFoundError: The snapshot is not in the network's listing.
        """
        resolved = self._network(network_id)
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            snapshot = self._find(resolved, snapshot_id)
            state = _state_of(snapshot)
            if state == PROCESSED:
                return snapshot
            if state in FAILED_STATES:
                raise ForwardExecutionError(
                    f"snapshot {snapshot_id} ended in state {state}",
                    status=state,
                    outcome=state,
                )
            if deadline is not None and time.monotonic() >= deadline:
                raise ForwardTimeoutError(
                    f"snapshot {snapshot_id} was still {state or 'unprocessed'} after "
                    f"{timeout}s; processing continues on Forward"
                )
            time.sleep(poll_interval)

    def compute_advanced_reachability(self, snapshot_id: str) -> Mapping[str, Any]:
        """Start advanced reachability computation for a snapshot."""
        return dict(
            self._send_json(ops.compute_advanced_reachability(snapshot_id=snapshot_id)) or {}
        )

    def start_reachability_job(
        self, snapshot_id: str, *, network_id: str | None = None
    ) -> ReachabilityJob:
        """Start reachability computation as a pollable job.

        Unpublished: these endpoints are outside Forward's documented API, though
        stable and safe to use. :meth:`compute_advanced_reachability` is the
        published equivalent, without progress polling.
        """
        resolved = self._network(network_id)
        payload = self._send_json(
            repo_ops.start_reachability(network_id=resolved, snapshot_id=snapshot_id)
        )
        data = dict(payload or {})
        key = data.get("jobKey") or data.get("executionKey") or data.get("id")
        if not key:
            raise ForwardExecutionError(
                f"Forward started reachability for snapshot {snapshot_id} "
                "but returned no job identifier"
            )
        return ReachabilityJob(
            self, key=str(key), network_id=resolved, snapshot_id=snapshot_id, status=data
        )

    def _find(self, network_id: str, snapshot_id: str) -> SnapshotInfo:
        for snapshot in self.list(network_id, include_archived=True):
            if snapshot.id == snapshot_id:
                return snapshot
        raise ForwardNotFoundError(
            f"snapshot {snapshot_id!r} is not in network {network_id}", status=404
        )

    def _nqe(self) -> NqeService:
        """The NQE service, for snapshot probes. Shares this client's transport."""
        if self._nqe_service is None:
            self._nqe_service = NqeService(self._transport)
        return self._nqe_service


class ReachabilityJob:
    """A reachability computation running on Forward.

    Unpublished; see :meth:`SnapshotsService.start_reachability_job`.
    """

    def __init__(
        self,
        service: SnapshotsService,
        *,
        key: str,
        network_id: str,
        snapshot_id: str,
        status: Mapping[str, Any] | None = None,
    ) -> None:
        self._service = service
        self.key = key
        self.network_id = network_id
        self.snapshot_id = snapshot_id
        self._status = dict(status or {})

    def __repr__(self) -> str:
        return f"<ReachabilityJob key={self.key!r} status={self.last_status!r}>"

    @property
    def last_status(self) -> str | None:
        value = self._status.get("status")
        return str(value) if value is not None else None

    def status(self) -> dict[str, Any]:
        payload = self._service._send_json(
            repo_ops.reachability_status(
                network_id=self.network_id,
                snapshot_id=self.snapshot_id,
                job_key=self.key,
            )
        )
        self._status = dict(payload or {})
        return self._status

    def wait(self, *, poll_interval: float = 5.0, timeout: float | None = 3600.0) -> dict[str, Any]:
        """Poll until the computation finishes."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            status = self.status()
            state = str(status.get("status") or "").upper()
            if state in REACHABILITY_DONE:
                return status
            if state in REACHABILITY_FAILED:
                raise ForwardExecutionError(
                    f"reachability job {self.key} ended in state {state}",
                    status=state,
                    outcome=state,
                )
            if deadline is not None and time.monotonic() >= deadline:
                raise ForwardTimeoutError(
                    f"reachability job {self.key} was still {state or 'pending'} after {timeout}s"
                )
            time.sleep(poll_interval)


def _processed_order(snapshot: SnapshotInfo) -> tuple[str, str]:
    """Sort key placing the most recently processed snapshot last.

    Forward's timestamps are ISO-8601 in UTC, which sorts correctly as text.
    ``createdAt`` breaks ties, and an absent value sorts first so a snapshot
    missing a timestamp never wins by accident.
    """
    return (str(snapshot.processed_at or ""), str(snapshot.created_at or ""))


def _state_param(
    state: str | SnapshotState | Sequence[str] | None,
) -> str | list[str] | None:
    if state is None:
        return None
    if isinstance(state, (str, SnapshotState)):
        return str(getattr(state, "value", state))
    return [str(getattr(item, "value", item)) for item in state]
