"""Request builders for T-API optical network containers.

Unpublished. A container is created from one or more T-API JSON documents
sent as multipart parts, which is why this family is written by hand rather
than derived from the operation table.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from forward_sdk._ops import RequestSpec, op, spec_for

#: A T-API document to upload: a path, or ``(filename, bytes)``.
TapiFile = Path | tuple[str, bytes]


@op("getTapiNetworkContainers")
def list_containers(*, network_id: str) -> RequestSpec:
    return spec_for("getTapiNetworkContainers", path_params={"networkId": network_id})


@op("getTapiNetworkContainer")
def get_container(*, network_id: str, container_name: str) -> RequestSpec:
    return spec_for(
        "getTapiNetworkContainer",
        path_params={"networkId": network_id, "containerName": container_name},
    )


@op("putTapiNetworkContainer")
def put_container(*, network_id: str, name: str, files: Sequence[TapiFile]) -> RequestSpec:
    """Create or replace a container from T-API documents.

    Several documents are merged into one model. Forward reads each part with
    its JSON reader, so a part that is not JSON is refused with 400 naming it.
    """
    if not files:
        raise ValueError("a T-API container needs at least one file")
    parts: list[tuple[str, Any]] = []
    for item in files:
        if isinstance(item, Path):
            parts.append(("file", (item.name, item.read_bytes(), "application/json")))
        else:
            filename, content = item
            parts.append(("file", (filename, content, "application/json")))
    return spec_for(
        "putTapiNetworkContainer",
        path_params={"networkId": network_id},
        files=parts,
        data={"name": name},
        idempotent=False,
    )


@op("deleteTapiNetworkContainers")
def delete_containers(*, network_id: str) -> RequestSpec:
    return spec_for("deleteTapiNetworkContainers", path_params={"networkId": network_id})


@op("deleteTapiNetworkContainer")
def delete_container(*, network_id: str, container_name: str) -> RequestSpec:
    return spec_for(
        "deleteTapiNetworkContainer",
        path_params={"networkId": network_id, "containerName": container_name},
    )


@op("backdateTapiNetworkContainers")
def backdate_containers(*, network_id: str, snapshot_id: str) -> RequestSpec:
    return spec_for(
        "backdateTapiNetworkContainers",
        path_params={"networkId": network_id},
        query={"snapshotId": snapshot_id},
        idempotent=False,
    )
