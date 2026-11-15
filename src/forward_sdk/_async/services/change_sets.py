"""Forward Predict change sets.

These endpoints are unpublished: absent from Forward's public API description,
so the SDK describes their shapes by hand in ``spec/unpublished.yaml``, from the
server source and a live instance. They are how a change is rehearsed: stage
device changes against a base snapshot, commit them, run Predict to produce a
snapshot of the network as it would be, then compare that snapshot to the base
with :mod:`forward_sdk._async.services.diffs`.

Two conventions differ from the published API and are handled here so callers
do not meet them. Query-string actions select operations, so predicting and
deleting are separate operations on one path. And Forward rejects a body with
an unknown property, so the create body carries no network id: the network is
the path's.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._generated.models import (
    BgpAdvertisement,
    BgpAdvertisementDiff,
    BgpAdvertisementKey,
    BgpCollectionRequirement,
    BgpCollectionRequirements,
    ChangeSet,
    ChangeSetCheckResult,
    ChangeSetCheckResults,
    ChangeSetCommit,
    ChangeSetDraft,
    ChangeSetDraftResponse,
    ChangeSetInfo,
    ChangeSetSummary,
    ClassicDevice,
    ClassicDevices,
    CommandValidationResponse,
    CommittedChangeSet,
    ConfigValues,
    PredictedSnapshotMeta,
    SnapshotInfo,
)
from forward_sdk._ops import change_sets as ops
from forward_sdk._ops import rest
from forward_sdk.bgp import (
    PREDICT_MODEL_BGP,
    PREDICT_MODEL_EXT_ADV,
    PREDICT_MODELING,
    BgpReadiness,
    BgpStagingReport,
    match_staging,
)
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardBgpKeyMatchedNothingError,
    ForwardNotFoundError,
    ForwardPermissionError,
)

if TYPE_CHECKING:  # pragma: no cover
    from forward_sdk._async.services.snapshots import AsyncSnapshotsService

#: The trigger Forward records on a snapshot it created to analyse a change set.
PREDICT_TRIGGER = "PREDICT"


class AsyncChangeSetsService(AsyncService):
    """Create, stage, commit and predict change sets."""

    def __init__(self, transport: Any, snapshots: AsyncSnapshotsService) -> None:
        super().__init__(transport)
        self._snapshots = snapshots

    async def create(
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
        payload = await self._send_json(
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

    async def list(self, network_id: str | None = None) -> list[ChangeSetInfo]:
        """A network's change sets, each with its predicted snapshots newest first."""
        payload = await self._send_json(ops.list_change_sets(network_id=self._network(network_id)))
        return [ChangeSetInfo.model_validate(row) for row in (payload or [])]

    def handle(self, change_set_id: str, *, network_id: str | None = None) -> AsyncChangeSetHandle:
        """A handle to an existing change set, by id, without a request."""
        return AsyncChangeSetHandle(
            self, network_id=self._network(network_id), change_set_id=change_set_id
        )

    async def delete(self, change_set_ids: Sequence[str], *, network_id: str | None = None) -> None:
        """Delete one or more change sets."""
        ids = list(change_set_ids)
        if not ids:
            return
        if len(ids) == 1:
            await self._transport.send(
                ops.delete(network_id=self._network(network_id), change_set_id=ids[0])
            )
        else:
            await self._transport.send(
                ops.delete_many(network_id=self._network(network_id), change_set_ids=ids)
            )

    async def tag(
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
            await self._transport.send(
                ops.update_tags(
                    network_id=resolved, change_set_ids=change_set_ids, action="REMOVE_ALL"
                )
            )
            return
        if add:
            await self._transport.send(
                ops.update_tags(
                    network_id=resolved, change_set_ids=change_set_ids, action="ADD", names=add
                )
            )
        if remove:
            await self._transport.send(
                ops.update_tags(
                    network_id=resolved,
                    change_set_ids=change_set_ids,
                    action="REMOVE",
                    names=remove,
                )
            )

    async def bgp_advertisement_readiness(
        self, device_name: str, *, network_id: str | None = None
    ) -> BgpReadiness:
        """What would make Predict silently model nothing for BGP staged on a device.

        Checks the org properties ``PREDICT_MODELING``, ``PREDICT_MODEL_BGP``
        and ``PREDICT_MODEL_EXT_ADV``; the device's collection setup
        (``collectBgpAdvertisements`` on, and a table type that includes
        ADJ_RIB_IN so there are received routes to replace or withdraw); and
        the network's BGP collection requirement groups. It only reads.

        A read refused for permission (403), or a route this Forward does not
        serve, is recorded in ``unknown`` and ``notes`` rather than raised, so a
        caller without access to the organization's configuration still gets
        the rest. So is a device with no classic collection source: a
        controller-managed device takes its BGP setting elsewhere, which this
        cannot check. ``ready`` is false while anything is unknown; ``blocked``
        is true only for a definite problem. For several devices use
        :meth:`bgp_advertisement_readiness_for`. Mirrors the Go SDK v0.8.32.
        """
        reports = await self.bgp_advertisement_readiness_for([device_name], network_id=network_id)
        return reports[0]

    async def bgp_advertisement_readiness_for(
        self, device_names: Sequence[str], *, network_id: str | None = None
    ) -> builtins.list[BgpReadiness]:
        """:meth:`bgp_advertisement_readiness` for several devices, in the order given.

        The three reads it is built on (org properties, the network's
        collection sources, its requirement groups) are the same for every
        device, so they are made once: three requests however many devices.
        """
        if isinstance(device_names, str):
            raise ValueError(
                "device_names takes a list of names; use bgp_advertisement_readiness for one"
            )
        names = [name.strip() for name in device_names]
        if not names or not all(names):
            raise ValueError("at least one device name is required, and none may be blank")
        network = self._network(network_id)
        shared = BgpReadiness(device="")

        def soft(error: ForwardAPIError, what: str, unknown: list[str]) -> None:
            refused = isinstance(error, ForwardPermissionError)
            unserved = isinstance(error, ForwardNotFoundError) and error.endpoint_not_served
            if not (refused or unserved):
                raise error
            reason = f"{what} could not be read: {error}"
            shared.unknown.extend(unknown)
            shared.unknown_reasons.update(dict.fromkeys(unknown, reason))
            shared.notes.append(reason)

        switches = (PREDICT_MODELING, PREDICT_MODEL_BGP, PREDICT_MODEL_EXT_ADV)
        try:
            payload = await self._send_json(rest.BUILDERS["getOrgConfigValues"](filter="OFF"))
        except ForwardAPIError as error:
            soft(error, "organization properties", ["organization-properties"])
        else:
            # Forward keys these by the lowercase property name (seen live on 26.11).
            raw = ConfigValues.model_validate(payload or {}).model_extra or {}
            values = {str(key).upper(): value for key, value in raw.items()}
            shared.predict_modeling = _bool_or_none(values.get(PREDICT_MODELING))
            shared.predict_model_bgp = _bool_or_none(values.get(PREDICT_MODEL_BGP))
            shared.predict_model_ext_adv = _bool_or_none(values.get(PREDICT_MODEL_EXT_ADV))
            absent = [n for n in switches if _bool_or_none(values.get(n)) is None]
            if absent:
                shared.unknown.extend(absent)
                shared.unknown_reasons.update(
                    dict.fromkeys(
                        absent,
                        "not returned by the organization properties API "
                        "(Forward's source defaults it to on)",
                    )
                )
                shared.notes.append(
                    "not returned by the organization properties API (Forward's source "
                    "defaults them to on): " + ", ".join(absent)
                )
        if shared.predict_modeling is False:
            shared.problems.append(
                "PREDICT_MODELING is off for the organization: Predict change sets are unavailable"
            )
        if shared.predict_model_bgp is False:
            shared.problems.append("PREDICT_MODEL_BGP is off: Predict does not model BGP")
        if shared.predict_model_ext_adv is False:
            shared.problems.append(
                "PREDICT_MODEL_EXT_ADV is off: protocol modeling does not seed routes from the "
                "collected BGP received-route tables"
            )

        devices = None
        try:
            payload = await self._send_json(rest.BUILDERS["getClassicDevices"](network_id=network))
        except ForwardAPIError as error:
            soft(error, "the network's collection sources", ["classic-devices"])
        else:
            devices = ClassicDevices.model_validate(payload or {}).devices or []

        groups = None
        try:
            payload = await self._send_json(
                rest.BUILDERS["getBgpCollectionRequirements"](network_id=network)
            )
        except ForwardAPIError as error:
            soft(
                error, "the network's BGP collection requirements", ["bgp-collection-requirements"]
            )
        else:
            groups = BgpCollectionRequirements.model_validate(payload or {}).requirements or []

        return [_device_readiness(name, shared, devices, groups) for name in names]


class AsyncChangeSetHandle:
    """One change set, and the workflow around it.

    Obtained from :meth:`AsyncChangeSetsService.create` or
    :meth:`AsyncChangeSetsService.handle`.
    """

    def __init__(
        self,
        service: AsyncChangeSetsService,
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

    async def summary(self) -> ChangeSetSummary:
        """Name, base snapshot and tags. The only read of a change set by id."""
        payload = await self._service._send_json(
            ops.summary(network_id=self.network_id, change_set_id=self.id)
        )
        return ChangeSetSummary.model_validate(payload or {})

    async def draft(self) -> ChangeSetDraft | None:
        """The staged, uncommitted changes, or ``None`` when nothing is staged."""
        payload = await self._service._send_json(
            ops.draft(network_id=self.network_id, change_set_id=self.id)
        )
        response = ChangeSetDraftResponse.model_validate(payload or {})
        return response.draft

    async def commits(self) -> list[CommittedChangeSet]:
        """The commit history, newest first, each with its device changes."""
        payload = await self._service._send_json(
            ops.commits(network_id=self.network_id, change_set_id=self.id)
        )
        return [CommittedChangeSet.model_validate(row) for row in (payload or [])]

    async def head(self) -> CommittedChangeSet:
        """The most recent commit with its device changes.

        Raises:
            ForwardNotFoundError: If nothing has been committed yet.
        """
        payload = await self._service._send_json(
            ops.head_commit(network_id=self.network_id, change_set_id=self.id)
        )
        return CommittedChangeSet.model_validate(payload or {})

    async def predicted_snapshots(self) -> list[SnapshotInfo]:
        """The snapshots Predict has produced for this change set, with state."""
        payload = await self._service._send_json(
            ops.predicted_snapshots(network_id=self.network_id, change_set_id=self.id)
        )
        return [SnapshotInfo.model_validate(row) for row in (payload or [])]

    # -- staging ---------------------------------------------------------

    async def set_commands(self, device_name: str, commands: str) -> None:
        """Stage CLI commands for a device, replacing whatever was staged.

        Sent as plain text. This is the route for devices whose configuration
        Forward can model from CLI. It does not work for PAN-OS firewalls:
        predicting fails with "Device <name> OS pan_os not supported", and
        firewall changes go through :attr:`~AsyncForwardClient.firewall_predict`
        instead.
        """
        await self._service._transport.send(
            ops.set_device_commands(
                network_id=self.network_id,
                change_set_id=self.id,
                device_name=device_name,
                commands=commands,
            )
        )

    async def validate_commands(
        self, device_name: str, commands: str, *, line: int = 1, column: int = 1
    ) -> CommandValidationResponse:
        """Check CLI text for a device without staging it."""
        payload = await self._service._send_json(
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

    async def discard_device(self, device_name: str) -> None:
        """Discard one device's uncommitted changes."""
        await self._service._transport.send(
            ops.discard_device_draft(
                network_id=self.network_id, change_set_id=self.id, device_name=device_name
            )
        )

    async def discard_device_changes(self, device_name: str) -> None:
        """Remove a device from the change set, so committing reverts it to the base.

        Unlike :meth:`discard_device`, which reverts the draft to the last commit,
        this drops every change the change set makes to the device, committed or
        not. 404 when the change set has no changes for it.
        """
        await self._service._transport.send(
            ops.discard_device_changes(
                network_id=self.network_id, change_set_id=self.id, device_name=device_name
            )
        )

    async def update(
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
        payload = await self._service._send_json(
            ops.update(network_id=self.network_id, change_set_id=self.id, changes=changes)
        )
        return ChangeSet.model_validate(payload or {})

    # -- committing and predicting ----------------------------------------

    async def commit(self, note: str, *, analysis_mode: str | None = None) -> ChangeSetCommit:
        """Commit the staged changes. ``note`` is required and must not be blank.

        ``analysis_mode`` is ``BEHAVIORAL`` or ``CONFIG_ONLY``. Forward honours it
        only when the organization property ``PREDICT_CONFIG_ONLY`` is on.
        """
        payload = await self._service._send_json(
            ops.commit(
                network_id=self.network_id,
                change_set_id=self.id,
                note=note,
                analysis_mode=analysis_mode,
            )
        )
        return ChangeSetCommit.model_validate(payload or {})

    async def predict(self, note: str) -> PredictedSnapshotMeta:
        """Run Predict and return the snapshot it created, not yet processed.

        ``note`` is a required query parameter on Forward's side; the SDK's
        signature merely reflects that. Use :meth:`predict_and_wait` to get the
        processed snapshot back.
        """
        payload = await self._service._send_json(
            ops.predict(network_id=self.network_id, change_set_id=self.id, note=note)
        )
        return PredictedSnapshotMeta.model_validate(payload or {})

    async def predict_and_wait(
        self, note: str, *, timeout: float | None = 3600.0, poll_interval: float | None = None
    ) -> SnapshotInfo:
        """Run Predict and wait for the predicted snapshot to finish processing.

        Returns the processed snapshot, whose id is the ``after`` side of every
        diff against the change set's base. A snapshot-ready webhook fires at
        the same moment this returns; if you drive the pipeline from that
        webhook instead, note that the subnet connectivity comparison has not
        started yet when it fires, and use the diffs service's wait.
        """
        started = await self.predict(note)
        if not started.id:
            raise ValueError("Forward did not return the predicted snapshot's id")
        kwargs: dict[str, Any] = {"network_id": self.network_id, "timeout": timeout}
        if poll_interval is not None:
            kwargs["poll_interval"] = poll_interval
        return await self._service._snapshots.wait_until_processed(str(started.id), **kwargs)

    # -- checks ----------------------------------------------------------

    async def add_check(
        self, definition: Mapping[str, Any], *, name: str | None = None
    ) -> ChangeSetCheckResult:
        """Attach an intent check. Supported types: ISOLATION, REACHABILITY, EXISTS, NQE.

        A name may be given for the first three and must be omitted for NQE.
        """
        payload = await self._service._send_json(
            ops.add_check(
                network_id=self.network_id, change_set_id=self.id, definition=definition, name=name
            )
        )
        return ChangeSetCheckResult.model_validate(payload or {})

    async def checks(self, *, snapshot_id: str | None = None) -> list[ChangeSetCheckResult]:
        """The checks and their results, against the base or a predicted snapshot."""
        payload = await self._service._send_json(
            ops.checks(network_id=self.network_id, change_set_id=self.id, snapshot_id=snapshot_id)
        )
        return list(ChangeSetCheckResults.model_validate(payload or {}).checks or [])

    async def delete_check(self, check_id: str) -> None:
        await self._service._transport.send(
            ops.delete_check(network_id=self.network_id, change_set_id=self.id, check_id=check_id)
        )

    async def verify_bgp_staging(
        self,
        device_name: str,
        *,
        staged: Sequence[BgpAdvertisement] = (),
        withdrawn: Sequence[BgpAdvertisementKey] = (),
    ) -> BgpStagingReport:
        """Check the draft's BGP diffs for every advertisement staged and key withdrawn.

        Reads the draft's diffs view for ``device_name`` and matches each key
        (vrf, external peer, prefix) to its row; see
        :func:`forward_sdk.bgp.match_staging`. A host prefix given as a bare
        address or as ``/32`` compares equal; Forward writes it back bare.

        Raises:
            ForwardBgpKeyMatchedNothingError: When a withdrawal reads
                ``ABSENT`` (Forward ignores it when the change set runs) or a
                key has no diff row. ``error.report`` holds the full read-back.
        """
        payload = await self._service._send_json(
            rest.BUILDERS["getDraftBgpAdvertisementDiffs"](
                network_id=self.network_id, change_set_id=self.id, device_name=device_name
            )
        )
        diffs = [BgpAdvertisementDiff.model_validate(row) for row in (payload or [])]
        report = match_staging(diffs, staged, withdrawn)
        if report.unmatched:
            raise ForwardBgpKeyMatchedNothingError(
                "a staged BGP key matched nothing: " + "; ".join(report.unmatched), report
            )
        return report

    # -- commits and configurations --------------------------------------

    async def commit_detail(self, commit_id: str) -> CommittedChangeSet:
        """One commit, with the device changes it carried."""
        payload = await self._service._send_json(
            rest.BUILDERS["getChangeSetCommit"](
                network_id=self.network_id, change_set_id=self.id, commit_id=commit_id
            )
        )
        return CommittedChangeSet.model_validate(payload or {})

    async def edit_commit_note(self, commit_id: str, note: str) -> ChangeSetCommit:
        """Replace a commit's note. The note must not be blank."""
        payload = await self._service._send_json(
            rest.BUILDERS["updateChangeSetCommitNote"](
                network_id=self.network_id, change_set_id=self.id, commit_id=commit_id, note=note
            )
        )
        return ChangeSetCommit.model_validate(payload or {})

    async def delete_commit(self, commit_id: str) -> None:
        """Delete a commit and the predicted snapshots made from it. Not reversible."""
        await self._service._transport.send(
            rest.BUILDERS["deleteChangeSetCommit"](
                network_id=self.network_id, change_set_id=self.id, commit_id=commit_id
            )
        )

    async def restore_commit(self, commit_id: str) -> ChangeSetCommit:
        """Add a new head commit carrying an earlier commit's device changes.

        A draft you have pending is saved as its own commit first, so one call can
        add two commits and leaves no draft behind. From Forward's source; not
        run against a live instance.
        """
        payload = await self._service._send_json(
            rest.BUILDERS["restoreChangeSetCommit"](
                network_id=self.network_id, change_set_id=self.id, commit_id=commit_id
            )
        )
        return ChangeSetCommit.model_validate(payload or {})

    async def restore_commit_as_new(
        self,
        commit_id: str,
        *,
        name: str | None = None,
        base_snapshot_id: str | None = None,
    ) -> CommittedChangeSet:
        """Start a new change set from a commit, leaving this one as it is."""
        payload = await self._service._send_json(
            rest.BUILDERS["restoreChangeSetCommitAsNew"](
                network_id=self.network_id,
                change_set_id=self.id,
                commit_id=commit_id,
                target_name=name,
                target_base_snapshot_id=base_snapshot_id,
            )
        )
        return CommittedChangeSet.model_validate(payload or {})

    async def duplicate(
        self,
        *,
        name: str | None = None,
        base_snapshot_id: str | None = None,
        target_network_id: str | None = None,
    ) -> ChangeSet:
        """Copy this change set, optionally into another network.

        A copy into another network needs ``base_snapshot_id``, a snapshot of that
        network to base it on.
        """
        if (
            target_network_id is not None
            and target_network_id != self.network_id
            and base_snapshot_id is None
        ):
            raise ValueError("copying to another network needs a base_snapshot_id there")
        body = {
            key: value
            for key, value in (("targetName", name), ("targetBaseSnapshotId", base_snapshot_id))
            if value is not None
        }
        if target_network_id is None:
            builder = ops.duplicate(network_id=self.network_id, change_set_id=self.id, body=body)
        else:
            builder = ops.duplicate_to_network(
                network_id=self.network_id,
                change_set_id=self.id,
                target_network_id=target_network_id,
                body=body,
            )
        return ChangeSet.model_validate(await self._service._send_json(builder) or {})

    async def draft_device_config(self, device_name: str) -> str:
        """A device's whole draft configuration, as text."""
        response = await self._service._transport.send(
            rest.BUILDERS["getChangeSetDraftDeviceConfig"](
                network_id=self.network_id, change_set_id=self.id, device_name=device_name
            )
        )
        return response.text

    async def save_draft_device_config(self, device_name: str, config: str) -> None:
        """Replace a device's whole draft configuration (at most 2 MB).

        Where :meth:`set_commands` stages commands to run, this replaces the
        configuration itself.
        """
        await self._service._transport.send(
            ops.save_draft_device_config(
                network_id=self.network_id,
                change_set_id=self.id,
                device_name=device_name,
                config=config,
            )
        )

    async def committed_device_config(self, commit_id: str, device_name: str) -> str:
        """A device's configuration as of a commit, as text."""
        response = await self._service._transport.send(
            rest.BUILDERS["getChangeSetCommittedDeviceConfig"](
                network_id=self.network_id,
                change_set_id=self.id,
                commit_id=commit_id,
                device_name=device_name,
            )
        )
        return response.text

    async def preview_device_config(self, device_name: str, commands: str) -> str:
        """What a device's configuration would be after ``commands``. Stages nothing."""
        response = await self._service._transport.send(
            ops.preview_device_config(
                network_id=self.network_id,
                change_set_id=self.id,
                device_name=device_name,
                commands=commands,
            )
        )
        return response.text

    async def delete(self) -> None:
        """Delete this change set."""
        await self._service._transport.send(
            ops.delete(network_id=self.network_id, change_set_id=self.id)
        )


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _device_readiness(
    name: str,
    shared: BgpReadiness,
    devices: list[ClassicDevice] | None,
    groups: list[BgpCollectionRequirement] | None,
) -> BgpReadiness:
    """One device's report, from the org-wide findings and the two network reads."""
    report = BgpReadiness(
        device=name,
        predict_modeling=shared.predict_modeling,
        predict_model_ext_adv=shared.predict_model_ext_adv,
        predict_model_bgp=shared.predict_model_bgp,
        problems=list(shared.problems),
        unknown=list(shared.unknown),
        unknown_reasons=dict(shared.unknown_reasons),
        notes=list(shared.notes),
    )
    if devices is not None:
        for device in devices:
            if device.name.lower() != name.lower():
                continue
            report.device_found = True
            report.collect_bgp_advertisements = device.collect_bgp_advertisements
            report.bgp_table_type = _enum_text(device.bgp_table_type)
            report.bgp_peer_type = _enum_text(device.bgp_peer_type)
            break
        if not report.device_found:
            # Not a problem found: a controller-managed device has no classic
            # source and takes its BGP setting elsewhere, which this cannot read.
            key = f"collection-source:{name}"
            reason = (
                f"the network has no classic collection source named {name} (a "
                "controller-managed device is configured elsewhere, and this read cannot check it)"
            )
            report.unknown.append(key)
            report.unknown_reasons[key] = reason
            report.notes.append(reason)
        elif not report.collect_bgp_advertisements:
            report.problems.append(
                "collectBgpAdvertisements is not on for the device, so there are no "
                "received routes to override or withdraw"
            )
        else:
            # Forward leaves bgpTableType and bgpPeerType out when they are BOTH.
            report.bgp_table_type = report.bgp_table_type or "BOTH"
            report.bgp_peer_type = report.bgp_peer_type or "BOTH"
            if report.bgp_table_type == "ADJ_RIB_OUT":
                report.problems.append(
                    "bgpTableType is ADJ_RIB_OUT: received routes are not collected; "
                    "use BOTH or ADJ_RIB_IN"
                )
    if groups is not None:
        report.required_by_network = any(
            member.lower() == name.lower() for group in groups for member in group.devices or []
        )
    return report
