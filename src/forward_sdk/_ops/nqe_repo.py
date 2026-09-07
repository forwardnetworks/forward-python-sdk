"""Request builders for the NQE query library.

These endpoints are unpublished: they are absent from Forward's public API
description, so their shapes are described by hand in ``spec/unpublished.yaml``
rather than derived from it. They are stable and safe to depend on -- Forward's
own integrations use them -- and there is no published alternative for
publishing queries from code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._ops import RequestSpec, op, spec_for

HEAD = "head"


@op("getRepositoryQueries")
def list_queries(
    *,
    repository: str = "org",
    commit_id: str = HEAD,
    path: str | None = None,
    with_source: bool = False,
) -> RequestSpec:
    """List queries in a repository, optionally including their source."""
    return spec_for(
        "getRepositoryQueries",
        path_params={"repository": repository, "commitId": commit_id or HEAD},
        query={"path": path, "with": "sourceCode" if with_source else None},
    )


@op("getOrgHeadCommit")
def head_commit() -> RequestSpec:
    return spec_for("getOrgHeadCommit")


@op("getQueryHistory")
def query_history(*, query_id: str) -> RequestSpec:
    return spec_for("getQueryHistory", path_params={"queryId": query_id})


@op("getDraftChanges")
def list_drafts() -> RequestSpec:
    return spec_for("getDraftChanges")


@op("addDraftChange")
def stage_change(
    *,
    action: str,
    path: str,
    source: str | None = None,
    basis: Mapping[str, str] | None = None,
) -> RequestSpec:
    """Stage an addition, an edit, or a new directory.

    Staging is not idempotent: repeating an add after the server accepted it
    fails, so a retry must not be attempted once a response was received.
    """
    body: dict[str, Any] = {}
    if source is not None:
        body["sourceCode"] = source
    if basis:
        body["basis"] = dict(basis)
    return spec_for(
        "addDraftChange",
        query={"action": action, "path": path},
        json=body or None,
        idempotent=False,
    )


@op("discardDraftChange")
def discard_change(*, path: str) -> RequestSpec:
    return spec_for("discardDraftChange", query={"path": path})


@op("commitDraftChanges")
def commit(
    *,
    paths: Sequence[str],
    title: str,
    body: str = "",
    access_settings: Sequence[Mapping[str, Any]] = (),
    dry_run: bool = False,
    snapshot_id: str | None = None,
) -> RequestSpec:
    """Commit staged changes, or validate them without committing."""
    payload: dict[str, Any] = {
        "paths": list(paths),
        "accessSettings": [dict(s) for s in access_settings],
    }
    if not dry_run:
        payload["message"] = {"title": title, "body": body}
    return spec_for(
        "commitDraftChanges",
        query={"dryRun": dry_run or None, "snapshotId": snapshot_id},
        json=payload,
        idempotent=dry_run,
    )


@op("startReachabilityJob")
def start_reachability(*, network_id: str, snapshot_id: str) -> RequestSpec:
    return spec_for(
        "startReachabilityJob",
        path_params={"networkId": network_id, "snapshotId": snapshot_id},
        idempotent=True,
    )


@op("getReachabilityJob")
def reachability_status(*, network_id: str, snapshot_id: str, job_key: str) -> RequestSpec:
    return spec_for(
        "getReachabilityJob",
        path_params={
            "networkId": network_id,
            "snapshotId": snapshot_id,
            "jobKey": job_key,
        },
    )
