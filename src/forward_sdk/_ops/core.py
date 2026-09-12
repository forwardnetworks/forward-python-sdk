"""Request builders for versions, networks, snapshots, devices and device tags."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from forward_sdk._ops import RequestSpec, op, spec_for

BINARY_ACCEPT = "application/octet-stream"


@op("getApiVersion")
def api_version() -> RequestSpec:
    return spec_for("getApiVersion")


# --- networks ---------------------------------------------------------------


@op("getNetworks")
def list_networks() -> RequestSpec:
    return spec_for("getNetworks")


@op("createNetwork")
def create_network(*, name: str) -> RequestSpec:
    """Create a network.

    Forward takes the name as a query parameter here and accepts no body; set a
    note afterwards with :func:`update_network`.
    """
    return spec_for("createNetwork", query={"name": name}, idempotent=False)


@op("updateNetwork")
def update_network(*, network_id: str, changes: Mapping[str, Any]) -> RequestSpec:
    return spec_for("updateNetwork", path_params={"networkId": network_id}, json=dict(changes))


@op("deleteNetwork")
def delete_network(*, network_id: str) -> RequestSpec:
    return spec_for("deleteNetwork", path_params={"networkId": network_id})


@op("createWorkspaceNetwork")
def create_workspace(
    *, network_id: str, name: str | None = None, retention_days: int | None = None
) -> RequestSpec:
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if retention_days is not None:
        body["retentionDays"] = retention_days
    return spec_for(
        "createWorkspaceNetwork",
        path_params={"networkId": network_id},
        json=body or None,
        idempotent=False,
    )


# --- snapshots --------------------------------------------------------------


@op("listNetworkSnapshots")
def list_snapshots(
    *,
    network_id: str,
    state: str | Sequence[str] | None = None,
    limit: int | None = None,
    include_archived: bool = False,
    **filters: Any,
) -> RequestSpec:
    """List a network's snapshots, newest first."""
    query: dict[str, Any] = {
        "state": state,
        "limit": limit,
        # Sent even when false, so the argument asserts something rather than
        # deferring to a server default that could change.
        "includeArchived": bool(include_archived),
    }
    query.update(filters)
    return spec_for("listNetworkSnapshots", path_params={"networkId": network_id}, query=query)


@op("getLatestProcessedSnapshot")
def latest_processed_snapshot(*, network_id: str) -> RequestSpec:
    """Deprecated by Forward; prefer listing snapshots filtered by state."""
    return spec_for("getLatestProcessedSnapshot", path_params={"networkId": network_id})


@op("getSnapshotMetrics")
def snapshot_metrics(*, snapshot_id: str) -> RequestSpec:
    return spec_for("getSnapshotMetrics", path_params={"snapshotId": snapshot_id})


@op("deleteSnapshot")
def delete_snapshot(*, snapshot_id: str) -> RequestSpec:
    return spec_for("deleteSnapshot", path_params={"snapshotId": snapshot_id})


@op("computeAdvancedReachability")
def compute_advanced_reachability(*, snapshot_id: str) -> RequestSpec:
    return spec_for(
        "computeAdvancedReachability",
        path_params={"snapshotId": snapshot_id},
        idempotent=True,
    )


@op("exportSnapshot")
def export_snapshot(*, snapshot_id: str, only: str | None = None) -> RequestSpec:
    """Download a snapshot as a zip archive."""
    return spec_for(
        "exportSnapshot",
        path_params={"snapshotId": snapshot_id},
        query={"only": only},
        accept=BINARY_ACCEPT,
        stream=True,
    )


@op("exportSnapshotSubset")
def export_snapshot_subset(
    *, snapshot_id: str, body: Mapping[str, Any], only: str | None = None
) -> RequestSpec:
    return spec_for(
        "exportSnapshotSubset",
        path_params={"snapshotId": snapshot_id},
        query={"only": only},
        json=dict(body),
        accept=BINARY_ACCEPT,
        stream=True,
        idempotent=True,
    )


@op("createSnapshot")
def upload_snapshot(
    *,
    network_id: str,
    files: Sequence[Path],
    note: str | None = None,
    process: bool = True,
    wait: bool = False,
) -> RequestSpec:
    """Upload one or more snapshot archives.

    Several files are merged into one snapshot, so they must not describe the
    same device. Uploading is asynchronous by default: Forward answers 202 with
    the new snapshot's identity while unpacking continues.
    """
    parts: list[tuple[str, Any]] = [
        ("file", (path.name, path.read_bytes(), "application/zip")) for path in files
    ]
    data: dict[str, Any] = {"process": "true" if process else "false"}
    # `async` is Forward's parameter name; sending false makes it block.
    data["async"] = "false" if wait else "true"
    if note:
        data["note"] = note
    return spec_for(
        "createSnapshot",
        path_params={"networkId": network_id},
        files=parts,
        data=data,
        idempotent=False,
    )


# --- devices ----------------------------------------------------------------


@op("getDevices")
def list_devices(
    *,
    network_id: str,
    snapshot_id: str | None = None,
    skip: int | None = None,
    limit: int | None = None,
    with_: Sequence[str] | None = None,
    **filters: Any,
) -> RequestSpec:
    """List devices, optionally filtered and paged."""
    query: dict[str, Any] = {
        "snapshotId": snapshot_id,
        "skip": skip,
        "limit": limit,
        "with": with_,
    }
    query.update(filters)
    return spec_for("getDevices", path_params={"networkId": network_id}, query=query)


@op("getDevice")
def get_device(
    *,
    network_id: str,
    device_name: str,
    snapshot_id: str | None = None,
    with_: Sequence[str] | None = None,
) -> RequestSpec:
    return spec_for(
        "getDevice",
        path_params={"networkId": network_id, "deviceName": device_name},
        query={"snapshotId": snapshot_id, "with": with_},
    )


@op("getDeviceFiles")
def list_device_files(
    *, network_id: str, device_name: str, snapshot_id: str | None = None
) -> RequestSpec:
    return spec_for(
        "getDeviceFiles",
        path_params={"networkId": network_id, "deviceName": device_name},
        query={"snapshotId": snapshot_id},
    )


@op("getDeviceFileContent")
def get_device_file(
    *,
    network_id: str,
    device_name: str,
    file_name: str,
    snapshot_id: str | None = None,
) -> RequestSpec:
    """Fetch one collected file, such as a device's running configuration."""
    return spec_for(
        "getDeviceFileContent",
        path_params={
            "networkId": network_id,
            "deviceName": device_name,
            "fileName": file_name,
        },
        query={"snapshotId": snapshot_id},
        accept="text/plain, application/json;q=0.5",
    )


@op("getMissingDevices")
def list_missing_devices(*, network_id: str, snapshot_id: str | None = None) -> RequestSpec:
    """List devices referenced by the network but absent from the snapshot."""
    return spec_for(
        "getMissingDevices",
        path_params={"networkId": network_id},
        query={"snapshotId": snapshot_id},
    )


# --- device tags ------------------------------------------------------------


@op("getDeviceTags")
def list_device_tags(*, network_id: str) -> RequestSpec:
    return spec_for("getDeviceTags", path_params={"networkId": network_id})


@op("getDeviceTagsWithDevices")
def list_device_tags_with_devices(*, network_id: str) -> RequestSpec:
    return spec_for("getDeviceTagsWithDevices", path_params={"networkId": network_id})


@op("getDeviceTag")
def get_device_tag(*, network_id: str, tag_name: str) -> RequestSpec:
    return spec_for("getDeviceTag", path_params={"networkId": network_id, "tagName": tag_name})


@op("getDeviceTagWithDevices")
def get_device_tag_with_devices(*, network_id: str, tag_name: str) -> RequestSpec:
    return spec_for(
        "getDeviceTagWithDevices",
        path_params={"networkId": network_id, "tagName": tag_name},
    )


@op("addDeviceTag")
def add_device_tag(*, network_id: str, tag: Mapping[str, Any]) -> RequestSpec:
    return spec_for(
        "addDeviceTag",
        path_params={"networkId": network_id},
        json=dict(tag),
        idempotent=False,
    )


@op("addDeviceTagsBatch")
def add_device_tags(*, network_id: str, tags: Sequence[Mapping[str, Any]]) -> RequestSpec:
    return spec_for(
        "addDeviceTagsBatch",
        path_params={"networkId": network_id},
        json=[dict(tag) for tag in tags],
        idempotent=False,
    )


@op("updateDeviceTag")
def update_device_tag(*, network_id: str, tag_name: str, changes: Mapping[str, Any]) -> RequestSpec:
    return spec_for(
        "updateDeviceTag",
        path_params={"networkId": network_id, "tagName": tag_name},
        json=dict(changes),
    )


@op("deleteDeviceTag")
def delete_device_tag(*, network_id: str, tag_name: str) -> RequestSpec:
    return spec_for("deleteDeviceTag", path_params={"networkId": network_id, "tagName": tag_name})


@op("addDeviceTagToDevices")
def add_tag_to_devices(*, network_id: str, tag_name: str, devices: Sequence[str]) -> RequestSpec:
    """Forward takes a DeviceSet, ``{"devices": [...]}``, not a bare array.

    A bare array is refused with "Cannot deserialize value of type DeviceSet
    from Array value", which the SDK sent for its first thirteen releases
    because the test fixture had been written from the same assumption.
    """
    return spec_for(
        "addDeviceTagToDevices",
        path_params={"networkId": network_id, "tagName": tag_name},
        json={"devices": list(devices)},
    )


@op("removeDeviceTagFromDevices")
def remove_tag_from_devices(
    *, network_id: str, tag_name: str, devices: Sequence[str]
) -> RequestSpec:
    return spec_for(
        "removeDeviceTagFromDevices",
        path_params={"networkId": network_id, "tagName": tag_name},
        json={"devices": list(devices)},
    )


@op("addDeviceTagsToDevices")
def add_tags_to_devices(*, network_id: str, body: Any) -> RequestSpec:
    return spec_for("addDeviceTagsToDevices", path_params={"networkId": network_id}, json=body)


@op("removeDeviceTagsFromDevices")
def remove_tags_from_devices(*, network_id: str, body: Any) -> RequestSpec:
    return spec_for("removeDeviceTagsFromDevices", path_params={"networkId": network_id}, json=body)


@op("removeAllDeviceTagsFromDevices")
def remove_all_tags_from_devices(*, network_id: str, devices: Sequence[str]) -> RequestSpec:
    return spec_for(
        "removeAllDeviceTagsFromDevices",
        path_params={"networkId": network_id},
        json=list(devices),
    )


@op("replaceAllDeviceTagsForDevices")
def replace_all_tags_for_devices(*, network_id: str, body: Any) -> RequestSpec:
    return spec_for(
        "replaceAllDeviceTagsForDevices", path_params={"networkId": network_id}, json=body
    )
