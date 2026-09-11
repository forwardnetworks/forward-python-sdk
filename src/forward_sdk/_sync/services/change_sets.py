# Generated from src/forward_sdk/_async/services/change_sets.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Forward Predict change sets.

These endpoints are unpublished: absent from Forward's public API description,
so the SDK describes their shapes by hand in ``spec/unpublished.yaml``, from the
server source and a live instance. They are how a change is rehearsed: stage
device changes against a base snapshot, commit them, run Predict to produce a
snapshot of the network as it would be, then compare that snapshot to the base
with :mod:`forward_sdk._sync.services.diffs`.

Two conventions differ from the published API and are handled here so callers
do not meet them. Query-string actions select operations, so predicting and
deleting are separate operations on one path. And Forward rejects a body with
an unknown property, so the create body carries no network id: the network is
the path's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from forward_sdk._generated.models import (
    ChangeSet,
    ChangeSetCheckResult,
    ChangeSetCheckResults,
    ChangeSetCommit,
    ChangeSetDraft,
    ChangeSetDraftResponse,
    ChangeSetInfo,
    ChangeSetSummary,
    CommandValidationResponse,
    CommittedChangeSet,
    PredictedSnapshotMeta,
    SnapshotInfo,
)
from forward_sdk._ops import change_sets as ops
from forward_sdk._sync.services._base import Service

if TYPE_CHECKING:  # pragma: no cover
    from forward_sdk._sync.services.snapshots import SnapshotsService

#: The trigger Forward records on a snapshot it created to analyse a change set.
PREDICT_TRIGGER = "PREDICT"


class ChangeSetsService(Service):
    """Create, stage, commit and predict change sets."""

    def __init__(self, transport: Any, snapshots: SnapshotsService) -> None:
        super().__init__(transport)
        self._snapshots = snapshots

    def create(
        self,
        name: str,
        *,
        snapshot_id: str,
        network_id: str | None = None,
        description: str | None = None,
        tags: Sequence[str] = (),
        dir_path: str | None = None,
    ) -> AsyncChangeSetHandle:
        """Create a change set against a base snapshot and return a handle to it.

        Pick the base deliberately. ``snapshots.latest_processed`` excludes
        predicted snapshots for exactly this reason: basing a change set on a
        prediction predicts a change against a change.
        """
        payload = self._send_json(
            ops.create(
                network_id=self._network(network_id),
                name=name,
                snapshot_id=snapshot_id,
                description=description,
                tags=tags or None,
                dir_path=dir_path,
            )
        )
        created = ChangeSet.model_validate(payload or {})
        return AsyncChangeSetHandle(self, network_id=self._network(network_id), created=created)

    def list(self, network_id: str | None = None) -> list[ChangeSetInfo]:
        """A network's change sets, each with its predicted snapshots newest first."""
        payload = self._send_json(ops.list_change_sets(network_id=self._network(network_id)))
        return [ChangeSetInfo.model_validate(row) for row in (payload or [])]

    def handle(self, change_set_id: str, *, network_id: str | None = None) -> AsyncChangeSetHandle:
        """A handle to an existing change set, by id, without a request."""
        return AsyncChangeSetHandle(
            self, network_id=self._network(network_id), change_set_id=change_set_id
        )

    def delete(self, change_set_ids: Sequence[str], *, network_id: str | None = None) -> None:
        """Delete one or more change sets."""
        ids = list(change_set_ids)
        if not ids:
            return
        if len(ids) == 1:
            self._transport.send(
                ops.delete(network_id=self._network(network_id), change_set_id=ids[0])
            )
        else:
            self._transport.send(
                ops.delete_many(network_id=self._network(network_id), change_set_ids=ids)
            )

    def tag(
        self,
        change_set_ids: Sequence[str],
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
        remove_all: bool = False,
        network_id: str | None = None,
    ) -> None:
        """Add or remove tags across several change sets."""
        resolved = self._network(network_id)
        if remove_all:
            self._transport.send(
                ops.update_tags(
                    network_id=resolved, change_set_ids=change_set_ids, action="REMOVE_ALL"
                )
            )
            return
        if add:
            self._transport.send(
                ops.update_tags(
                    network_id=resolved, change_set_ids=change_set_ids, action="ADD", names=add
                )
            )
        if remove:
            self._transport.send(
                ops.update_tags(
                    network_id=resolved,
                    change_set_ids=change_set_ids,
                    action="REMOVE",
                    names=remove,
                )
            )


class AsyncChangeSetHandle:
    """One change set, and the workflow around it.

    Obtained from :meth:`ChangeSetsService.create` or
    :meth:`ChangeSetsService.handle`.
    """

    def __init__(
        self,
        service: ChangeSetsService,
        *,
        network_id: str,
        change_set_id: str | None = None,
        created: ChangeSet | None = None,
    ) -> None:
        self._service = service
        self.network_id = network_id
        self.created = created
        resolved = change_set_id or (created.id if created else None)
        if not resolved:
            raise ValueError("a change set handle needs an id")
        self.id: str = str(resolved)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id} on network {self.network_id}>"

    # -- reading ---------------------------------------------------------

    def summary(self) -> ChangeSetSummary:
        """Name, base snapshot and tags. The only read of a change set by id."""
        payload = self._service._send_json(
            ops.summary(network_id=self.network_id, change_set_id=self.id)
        )
        return ChangeSetSummary.model_validate(payload or {})

    def draft(self) -> ChangeSetDraft | None:
        """The staged, uncommitted changes, or ``None`` when nothing is staged."""
        payload = self._service._send_json(
            ops.draft(network_id=self.network_id, change_set_id=self.id)
        )
        response = ChangeSetDraftResponse.model_validate(payload or {})
        return response.draft

    def commits(self) -> list[CommittedChangeSet]:
        """The commit history, newest first, each with its device changes."""
        payload = self._service._send_json(
            ops.commits(network_id=self.network_id, change_set_id=self.id)
        )
        return [CommittedChangeSet.model_validate(row) for row in (payload or [])]

    def head(self) -> CommittedChangeSet:
        """The most recent commit with its device changes.

        Raises:
            ForwardNotFoundError: If nothing has been committed yet.
        """
        payload = self._service._send_json(
            ops.head_commit(network_id=self.network_id, change_set_id=self.id)
        )
        return CommittedChangeSet.model_validate(payload or {})

    def predicted_snapshots(self) -> list[SnapshotInfo]:
        """The snapshots Predict has produced for this change set, with state."""
        payload = self._service._send_json(
            ops.predicted_snapshots(network_id=self.network_id, change_set_id=self.id)
        )
        return [SnapshotInfo.model_validate(row) for row in (payload or [])]

    # -- staging ---------------------------------------------------------

    def set_commands(self, device_name: str, commands: str) -> None:
        """Stage CLI commands for a device, replacing whatever was staged.

        Sent as plain text. This is the route for devices whose configuration
        Forward can model from CLI. It does not work for PAN-OS firewalls:
        predicting fails with "Device <name> OS pan_os not supported", and
        firewall changes go through :attr:`~ForwardClient.firewall_predict`
        instead.
        """
        self._service._transport.send(
            ops.set_device_commands(
                network_id=self.network_id,
                change_set_id=self.id,
                device_name=device_name,
                commands=commands,
            )
        )

    def validate_commands(
        self, device_name: str, commands: str, *, line: int = 1, column: int = 1
    ) -> CommandValidationResponse:
        """Check CLI text for a device without staging it."""
        payload = self._service._send_json(
            ops.validate_device_commands(
                network_id=self.network_id,
                change_set_id=self.id,
                device_name=device_name,
                commands=commands,
                cursor_line=line,
                cursor_column=column,
            )
        )
        return CommandValidationResponse.model_validate(payload or {})

    def discard_device(self, device_name: str) -> None:
        """Discard one device's uncommitted changes."""
        self._service._transport.send(
            ops.discard_device_draft(
                network_id=self.network_id, change_set_id=self.id, device_name=device_name
            )
        )

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        snapshot_id: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ChangeSet:
        """Change the name, description, base snapshot or tags. Unset means unchanged."""
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if description is not None:
            changes["description"] = description
        if snapshot_id is not None:
            changes["snapshotId"] = snapshot_id
        if tags is not None:
            changes["tags"] = list(tags)
        payload = self._service._send_json(
            ops.update(network_id=self.network_id, change_set_id=self.id, changes=changes)
        )
        return ChangeSet.model_validate(payload or {})

    # -- committing and predicting ----------------------------------------

    def commit(self, note: str) -> ChangeSetCommit:
        """Commit the staged changes. ``note`` is required and must not be blank."""
        payload = self._service._send_json(
            ops.commit(network_id=self.network_id, change_set_id=self.id, note=note)
        )
        return ChangeSetCommit.model_validate(payload or {})

    def predict(self, note: str) -> PredictedSnapshotMeta:
        """Run Predict and return the snapshot it created, not yet processed.

        ``note`` is a required query parameter on Forward's side; the SDK's
        signature merely reflects that. Use :meth:`predict_and_wait` to get the
        processed snapshot back.
        """
        payload = self._service._send_json(
            ops.predict(network_id=self.network_id, change_set_id=self.id, note=note)
        )
        return PredictedSnapshotMeta.model_validate(payload or {})

    def predict_and_wait(
        self, note: str, *, timeout: float | None = 3600.0, poll_interval: float | None = None
    ) -> SnapshotInfo:
        """Run Predict and wait for the predicted snapshot to finish processing.

        Returns the processed snapshot, whose id is the ``after`` side of every
        diff against the change set's base. A snapshot-ready webhook fires at
        the same moment this returns; if you drive the pipeline from that
        webhook instead, note that the subnet connectivity comparison has not
        started yet when it fires, and use the diffs service's wait.
        """
        started = self.predict(note)
        if not started.id:
            raise ValueError("Forward did not return the predicted snapshot's id")
        kwargs: dict[str, Any] = {"network_id": self.network_id, "timeout": timeout}
        if poll_interval is not None:
            kwargs["poll_interval"] = poll_interval
        return self._service._snapshots.wait_until_processed(str(started.id), **kwargs)

    # -- checks ----------------------------------------------------------

    def add_check(
        self, definition: Mapping[str, Any], *, name: str | None = None
    ) -> ChangeSetCheckResult:
        """Attach an intent check. Supported types: ISOLATION, REACHABILITY, EXISTS, NQE.

        A name may be given for the first three and must be omitted for NQE.
        """
        payload = self._service._send_json(
            ops.add_check(
                network_id=self.network_id, change_set_id=self.id, definition=definition, name=name
            )
        )
        return ChangeSetCheckResult.model_validate(payload or {})

    def checks(self, *, snapshot_id: str | None = None) -> list[ChangeSetCheckResult]:
        """The checks and their results, against the base or a predicted snapshot."""
        payload = self._service._send_json(
            ops.checks(network_id=self.network_id, change_set_id=self.id, snapshot_id=snapshot_id)
        )
        return list(ChangeSetCheckResults.model_validate(payload or {}).checks or [])

    def delete_check(self, check_id: str) -> None:
        self._service._transport.send(
            ops.delete_check(network_id=self.network_id, change_set_id=self.id, check_id=check_id)
        )

    def delete(self) -> None:
        """Delete this change set."""
        self._service._transport.send(ops.delete(network_id=self.network_id, change_set_id=self.id))
