"""Request builders for Forward Predict change sets.

Unpublished: absent from Forward's generated description, described by hand in
``spec/unpublished.yaml``. Two conventions differ from the published API. A
query-string action selects the operation, so predict and delete are distinct
operations on one path, and Forward rejects unknown body properties with 400,
so bodies carry exactly the documented keys.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._ops import RequestSpec, op, spec_for

TEXT = "text/plain; charset=utf-8"


def _cs(network_id: str, change_set_id: str | None = None, **more: str) -> dict[str, str]:
    params = {"networkId": network_id}
    if change_set_id is not None:
        params["changeSetId"] = change_set_id
    params.update(more)
    return params


@op("createChangeSet")
def create(
    *,
    network_id: str,
    name: str,
    snapshot_id: str,
    description: str | None = None,
    tags: Sequence[str] | None = None,
    dir_path: str | None = None,
) -> RequestSpec:
    """Create a change set. The network comes from the path, never the body."""
    body: dict[str, Any] = {"name": name, "snapshotId": snapshot_id}
    if description is not None:
        body["description"] = description
    if tags:
        body["tags"] = list(tags)
    return spec_for(
        "createChangeSet",
        path_params=_cs(network_id),
        query={"dirPath": dir_path},
        json=body,
    )


@op("listChangeSets")
def list_change_sets(*, network_id: str) -> RequestSpec:
    return spec_for("listChangeSets", path_params=_cs(network_id))


@op("updateChangeSetTags")
def update_tags(
    *, network_id: str, change_set_ids: Sequence[str], action: str, names: Sequence[str] = ()
) -> RequestSpec:
    update: dict[str, Any] = {"tags": {"action": action}}
    if names:
        update["tags"]["names"] = list(names)
    return spec_for(
        "updateChangeSetTags",
        path_params=_cs(network_id),
        json={"changeSetIds": list(change_set_ids), "update": update},
    )


@op("deleteChangeSets")
def delete_many(*, network_id: str, change_set_ids: Sequence[str]) -> RequestSpec:
    """The key is changeSetIds; Forward refuses ids."""
    return spec_for(
        "deleteChangeSets",
        path_params=_cs(network_id),
        json={"changeSetIds": list(change_set_ids)},
    )


@op("deleteChangeSet")
def delete(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("deleteChangeSet", path_params=_cs(network_id, change_set_id))


@op("updateChangeSet")
def update(*, network_id: str, change_set_id: str, changes: Mapping[str, Any]) -> RequestSpec:
    return spec_for(
        "updateChangeSet", path_params=_cs(network_id, change_set_id), json=dict(changes)
    )


@op("getChangeSetSummary")
def summary(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("getChangeSetSummary", path_params=_cs(network_id, change_set_id))


@op("predictChangeSet")
def predict(*, network_id: str, change_set_id: str, note: str) -> RequestSpec:
    """Run Predict. ``note`` is required by Forward, not merely by this signature."""
    return spec_for(
        "predictChangeSet",
        path_params=_cs(network_id, change_set_id),
        query={"note": note},
        # Starting a prediction is not idempotent: a retry after an ambiguous
        # failure could create a second predicted snapshot.
        idempotent=False,
    )


@op("listPredictedSnapshots")
def predicted_snapshots(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("listPredictedSnapshots", path_params=_cs(network_id, change_set_id))


@op("getChangeSetDraft")
def draft(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("getChangeSetDraft", path_params=_cs(network_id, change_set_id))


@op("setChangeSetDeviceCommands")
def set_device_commands(
    *, network_id: str, change_set_id: str, device_name: str, commands: str
) -> RequestSpec:
    """Stage CLI text for a device. The body is the text itself, not JSON."""
    return spec_for(
        "setChangeSetDeviceCommands",
        path_params=_cs(network_id, change_set_id, deviceName=device_name),
        content=commands.encode("utf-8"),
        headers={"Content-Type": TEXT},
    )


@op("discardChangeSetDeviceDraft")
def discard_device_draft(*, network_id: str, change_set_id: str, device_name: str) -> RequestSpec:
    return spec_for(
        "discardChangeSetDeviceDraft",
        path_params=_cs(network_id, change_set_id, deviceName=device_name),
    )


@op("validateChangeSetDeviceCommands")
def validate_device_commands(
    *,
    network_id: str,
    change_set_id: str,
    device_name: str,
    commands: str,
    cursor_line: int = 1,
    cursor_column: int = 1,
) -> RequestSpec:
    return spec_for(
        "validateChangeSetDeviceCommands",
        path_params=_cs(network_id, change_set_id, deviceName=device_name),
        json={"commands": commands, "cursorLineNum": cursor_line, "cursorColumnNum": cursor_column},
        idempotent=True,
    )


@op("commitChangeSet")
def commit(*, network_id: str, change_set_id: str, note: str) -> RequestSpec:
    return spec_for(
        "commitChangeSet",
        path_params=_cs(network_id, change_set_id),
        query={"note": note},
        idempotent=False,
    )


@op("listChangeSetCommits")
def commits(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("listChangeSetCommits", path_params=_cs(network_id, change_set_id))


@op("getChangeSetHeadCommit")
def head_commit(*, network_id: str, change_set_id: str) -> RequestSpec:
    return spec_for("getChangeSetHeadCommit", path_params=_cs(network_id, change_set_id))


@op("addChangeSetCheck")
def add_check(
    *, network_id: str, change_set_id: str, definition: Mapping[str, Any], name: str | None = None
) -> RequestSpec:
    body: dict[str, Any] = {"definition": dict(definition)}
    if name is not None:
        body["name"] = name
    return spec_for("addChangeSetCheck", path_params=_cs(network_id, change_set_id), json=body)


@op("listChangeSetChecks")
def checks(*, network_id: str, change_set_id: str, snapshot_id: str | None = None) -> RequestSpec:
    return spec_for(
        "listChangeSetChecks",
        path_params=_cs(network_id, change_set_id),
        query={"snapshotId": snapshot_id},
    )


@op("deleteChangeSetCheck")
def delete_check(*, network_id: str, change_set_id: str, check_id: str) -> RequestSpec:
    return spec_for(
        "deleteChangeSetCheck",
        path_params=_cs(network_id, change_set_id, checkId=check_id),
    )
