"""Request builders for NQE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._ops import JSON_ACCEPT, NDJSON_ACCEPT, RequestSpec, op, spec_for
from forward_sdk.nqe.query_ref import QueryRef

#: Complex values render as JSON. The alternative, LEGACY, stringifies them and
#: is slated for removal by Forward.
ITEM_FORMAT_JSON = "JSON"


@op("runNqeQuery")
def run_query(
    ref: QueryRef,
    *,
    network_id: str | None = None,
    snapshot_id: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    column_filters: Sequence[Mapping[str, Any]] | None = None,
    sort_by: Mapping[str, Any] | None = None,
) -> RequestSpec:
    """Run a query and return one page of results.

    Paging for this endpoint lives in the body, under ``queryOptions``; the
    asynchronous execution endpoints take offset and limit as query parameters
    instead. That asymmetry is Forward's, and the SDK surfaces it rather than
    pretending both work the same way.
    """
    options: dict[str, Any] = {"offset": offset, "itemFormat": ITEM_FORMAT_JSON}
    if limit is not None:
        options["limit"] = limit
    if column_filters:
        options["columnFilters"] = [dict(f) for f in column_filters]
    if sort_by:
        options["sortBy"] = dict(sort_by)

    body = ref.to_payload(include_sort=False)
    body["queryOptions"] = options
    return spec_for(
        "runNqeQuery",
        query={"networkId": network_id, "snapshotId": snapshot_id},
        json=body,
        # Running a query has no side effects, so a retry is safe despite POST.
        idempotent=True,
    )


@op("addNqeQueryExecution")
def start_execution(
    ref: QueryRef,
    *,
    network_id: str,
    snapshot_id: str | None = None,
    column_filters: Sequence[Mapping[str, Any]] | None = None,
    use_latest_data_files: bool | None = None,
) -> RequestSpec:
    """Ask Forward to start running a query in the background."""
    body = ref.to_payload()
    if column_filters:
        body["columnFilters"] = [dict(f) for f in column_filters]
    if use_latest_data_files is not None:
        body["useLatestDataFiles"] = bool(use_latest_data_files)
    return spec_for(
        "addNqeQueryExecution",
        path_params={"networkId": network_id},
        query={"snapshotId": snapshot_id},
        json=body,
        idempotent=True,
    )


@op("getNqeExecutionStatus")
def execution_status(*, network_id: str, execution_key: str) -> RequestSpec:
    return spec_for(
        "getNqeExecutionStatus",
        path_params={"networkId": network_id, "executionKey": execution_key},
    )


@op("getNqeExecutionResultKey")
def execution_status_with_result_key(*, network_id: str, execution_key: str) -> RequestSpec:
    """The status as the UI reads it, which carries the result key."""
    return spec_for(
        "getNqeExecutionResultKey",
        path_params={"networkId": network_id, "executionKey": execution_key},
    )


@op("getNqeExecutionResult")
def execution_result(
    *,
    network_id: str,
    execution_key: str,
    offset: int | None = None,
    limit: int | None = None,
    accept: str = JSON_ACCEPT,
    stream: bool = False,
) -> RequestSpec:
    """Fetch results of a finished execution.

    Omitting ``limit`` asks for the whole result set in one response, which is
    what the streaming path uses.
    """
    return spec_for(
        "getNqeExecutionResult",
        path_params={"networkId": network_id, "executionKey": execution_key},
        query={"offset": offset, "limit": limit},
        accept=accept,
        stream=stream,
    )


def execution_stream(*, network_id: str, execution_key: str) -> RequestSpec:
    """Stream a finished execution's rows as newline-delimited JSON."""
    return execution_result(
        network_id=network_id,
        execution_key=execution_key,
        accept=NDJSON_ACCEPT,
        stream=True,
    )


@op("getNqeQueryDiff")
def diff(
    *,
    before_snapshot_id: str,
    after_snapshot_id: str,
    query_id: str,
    commit_id: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> RequestSpec:
    """Compare a query's results between two snapshots."""
    options: dict[str, Any] = {"offset": offset}
    if limit is not None:
        options["limit"] = limit

    body: dict[str, Any] = {"queryId": query_id, "options": options}
    if commit_id:
        body["commitId"] = commit_id
    if parameters:
        body["parameters"] = dict(parameters)

    return spec_for(
        "getNqeQueryDiff",
        path_params={"before": before_snapshot_id, "after": after_snapshot_id},
        json=body,
        idempotent=True,
    )


@op("getNqeQueries")
def list_queries(*, directory: str | None = None) -> RequestSpec:
    """List queries in the library, optionally under one directory."""
    return spec_for("getNqeQueries", query={"dir": directory})
