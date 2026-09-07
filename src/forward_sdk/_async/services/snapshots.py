"""Snapshots: the point-in-time models Forward builds from collected data.

Most reads are against a snapshot. Passing ``snapshot_id=None`` anywhere in the
SDK means "the network's latest processed snapshot", which Forward implements by
omitting the parameter, so no extra request is needed to resolve it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._async.services.nqe import AsyncNqeService
from forward_sdk._generated.models import SnapshotInfo, SnapshotState
from forward_sdk._ops import core as ops
from forward_sdk._ops import nqe_repo as repo_ops
from forward_sdk.errors import (
    ForwardExecutionError,
    ForwardNotFoundError,
    ForwardTimeoutError,
)
from forward_sdk.nqe.where import tag_scope

__all__ = ["AsyncSnapshotsService"]

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


def _state_of(snapshot: SnapshotInfo) -> str:
    return str(snapshot.state) if snapshot.state is not None else ""


class AsyncSnapshotsService(AsyncService):
    """List, inspect, upload and wait on snapshots."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self._nqe_service: AsyncNqeService | None = None

    async def list(
        self,
        network_id: str | None = None,
        *,
        state: str | SnapshotState | Sequence[str] | None = None,
        limit: int | None = None,
        include_archived: bool = False,
        **filters: Any,
    ) -> list[SnapshotInfo]:
        """List a network's snapshots, newest first."""
        payload = await self._send_json(
            ops.list_snapshots(
                network_id=self._network(network_id),
                state=_state_param(state),
                limit=limit,
                include_archived=include_archived,
                **filters,
            )
        )
        rows = (payload or {}).get("snapshots") or []
        return [SnapshotInfo.model_validate(row) for row in rows]

    async def latest_processed(self, network_id: str | None = None) -> SnapshotInfo | None:
        """The most recent fully processed snapshot, or ``None`` if there is none.

        Uses the snapshot listing rather than Forward's dedicated endpoint,
        which is deprecated.
        """
        snapshots = await self.list(network_id, state=PROCESSED, limit=1)
        return snapshots[0] if snapshots else None

    async def latest_processed_id(self, network_id: str | None = None) -> str | None:
        snapshot = await self.latest_processed(network_id)
        return snapshot.id if snapshot else None

    async def latest_collected_id(
        self,
        network_id: str | None = None,
        *,
        scan_limit: int = 10,
        include_tags: Sequence[str] = (),
        exclude_tags: Sequence[str] = (),
        include_match: str = "any",
    ) -> str:
        """Find the newest snapshot that actually contains collected devices.

        A snapshot can be processed and still hold nothing useful, for instance
        when collection failed for every device in scope. Callers that need real
        data (a sync job, say) want this rather than simply the latest processed
        snapshot. Each candidate is probed with a one-row query.

        Raises:
            ForwardNotFoundError: If no scanned snapshot has devices in scope.
        """
        resolved = self._network(network_id)
        scope = tag_scope(
            include=include_tags,
            exclude=exclude_tags,
            include_match=include_match,  # type: ignore[arg-type]
        )
        probe = COLLECTED_PROBE.format(scope=scope)

        candidates = await self.list(resolved, state=PROCESSED, limit=scan_limit)
        for snapshot in candidates:
            if not snapshot.id:
                continue
            result = await self._nqe().run(
                probe, network_id=resolved, snapshot_id=snapshot.id, limit=1
            )
            if result.items:
                return snapshot.id

        raise ForwardNotFoundError(
            f"none of the {len(candidates)} most recent processed snapshots of network "
            f"{resolved} contain devices in scope",
            status=404,
        )

    async def metrics(self, snapshot_id: str) -> Mapping[str, Any]:
        """Collection and processing counts for a snapshot."""
        return dict(await self._send_json(ops.snapshot_metrics(snapshot_id=snapshot_id)) or {})

    async def is_complete(self, snapshot_id: str) -> bool:
        """Whether a snapshot collected and processed everything without failures."""
        metrics = await self.metrics(snapshot_id)
        return not any(
            int(metrics.get(key) or 0)
            for key in (
                "numCollectionFailureDevices",
                "numProcessingFailureDevices",
                "numCollectionFailureEndpoints",
                "numProcessingFailureEndpoints",
            )
        )

    async def delete(self, snapshot_id: str) -> None:
        await self._send_json(ops.delete_snapshot(snapshot_id=snapshot_id))

    async def upload(
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

        payload = await self._send_json(
            ops.upload_snapshot(
                network_id=self._network(network_id),
                files=paths,
                note=note,
                process=process,
                wait=wait,
            )
        )
        return SnapshotInfo.model_validate(payload or {})

    async def export(self, snapshot_id: str, *, only: str | None = None) -> AsyncIterator[bytes]:
        """Download a snapshot archive in chunks.

        Args:
            only: Restrict the export, for example to ``CONFIG``.
        """
        spec = ops.export_snapshot(snapshot_id=snapshot_id, only=only)
        async with self._transport.stream(spec) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    async def download(self, snapshot_id: str, destination: str | Path, **kwargs: Any) -> Path:
        """Download a snapshot archive to a file."""
        path = Path(destination)
        with path.open("wb") as handle:
            async for chunk in self.export(snapshot_id, **kwargs):
                handle.write(chunk)
        return path

    async def wait_until_processed(
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
            snapshot = await self._find(resolved, snapshot_id)
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
            await asyncio.sleep(poll_interval)

    async def compute_advanced_reachability(self, snapshot_id: str) -> Mapping[str, Any]:
        """Start advanced reachability computation for a snapshot."""
        return dict(
            await self._send_json(ops.compute_advanced_reachability(snapshot_id=snapshot_id)) or {}
        )

    async def start_reachability_job(
        self, snapshot_id: str, *, network_id: str | None = None
    ) -> AsyncReachabilityJob:
        """Start reachability computation as a pollable job.

        Unpublished: these endpoints are outside Forward's documented API, though
        stable and safe to use. :meth:`compute_advanced_reachability` is the
        published equivalent, without progress polling.
        """
        resolved = self._network(network_id)
        payload = await self._send_json(
            repo_ops.start_reachability(network_id=resolved, snapshot_id=snapshot_id)
        )
        data = dict(payload or {})
        key = data.get("jobKey") or data.get("executionKey") or data.get("id")
        if not key:
            raise ForwardExecutionError(
                f"Forward started reachability for snapshot {snapshot_id} "
                "but returned no job identifier"
            )
        return AsyncReachabilityJob(
            self, key=str(key), network_id=resolved, snapshot_id=snapshot_id, status=data
        )

    async def _find(self, network_id: str, snapshot_id: str) -> SnapshotInfo:
        for snapshot in await self.list(network_id, include_archived=True):
            if snapshot.id == snapshot_id:
                return snapshot
        raise ForwardNotFoundError(
            f"snapshot {snapshot_id!r} is not in network {network_id}", status=404
        )

    def _nqe(self) -> AsyncNqeService:
        """The NQE service, for snapshot probes. Shares this client's transport."""
        if self._nqe_service is None:
            self._nqe_service = AsyncNqeService(self._transport)
        return self._nqe_service


class AsyncReachabilityJob:
    """A reachability computation running on Forward.

    Unpublished; see :meth:`AsyncSnapshotsService.start_reachability_job`.
    """

    def __init__(
        self,
        service: AsyncSnapshotsService,
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

    async def status(self) -> dict[str, Any]:
        payload = await self._service._send_json(
            repo_ops.reachability_status(
                network_id=self.network_id,
                snapshot_id=self.snapshot_id,
                job_key=self.key,
            )
        )
        self._status = dict(payload or {})
        return self._status

    async def wait(
        self, *, poll_interval: float = 5.0, timeout: float | None = 3600.0
    ) -> dict[str, Any]:
        """Poll until the computation finishes."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            status = await self.status()
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
            await asyncio.sleep(poll_interval)


def _state_param(
    state: str | SnapshotState | Sequence[str] | None,
) -> str | list[str] | None:
    if state is None:
        return None
    if isinstance(state, (str, SnapshotState)):
        return str(getattr(state, "value", state))
    return [str(getattr(item, "value", item)) for item in state]
