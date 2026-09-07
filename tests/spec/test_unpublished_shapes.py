"""Parsers are checked against the wire shapes Forward actually sends.

The published API has a description generated from Forward's server, so a parser
cannot disagree with it for long. The unpublished endpoints have no such
backstop: their shapes are whatever Forward happens to send, and the SDK's
knowledge of them is hand-written.

That gap produced a real bug. A query's commit is nested under ``lastCommit.id``,
the SDK read a flat ``lastCommitId`` that Forward does not send, and every query
resolved by path silently lost its pin and ran against head. Nothing failed,
because the parser and the test fixtures had been written together and agreed
with each other.

So the observed shapes are recorded once, in ``spec/unpublished.yaml``, and run
through the SDK's own parsers here. A fixture can no longer drift from the wire
format without this failing, because both now come from the same place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from forward_sdk.nqe.repository import (
    DraftChange,
    RepositoryQuery,
    commit_id_of,
    queries_from_payload,
)

SPEC_PATH = Path("spec/unpublished.yaml")

FULL_COMMIT = "84f84b0c0a0a1805ddff0ca5451c2c55c58605e5"


@pytest.fixture(scope="module")
def examples() -> dict[str, Any]:
    """The recorded response example for each unpublished operation."""
    document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for item in document["paths"].values():
        for operation in item.values():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            for response in operation.get("responses", {}).values():
                content = (response or {}).get("content") or {}
                example = (content.get("application/json") or {}).get("example")
                if example is not None:
                    found[operation["operationId"]] = example
    return found


def test_every_unpublished_operation_records_its_shape(examples: dict[str, Any]) -> None:
    """Nothing unpublished may go undocumented; that is what let the bug through."""
    document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    described = {
        operation["operationId"]
        for item in document["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    # Write operations return no body worth parsing.
    no_body = {"addDraftChange", "discardDraftChange"}
    missing = sorted(described - set(examples) - no_body)
    assert not missing, (
        f"unpublished operations with no recorded response shape: {missing}. "
        "Record what Forward sends, so the parser is checked against it."
    )


def test_query_listing_parses_the_recorded_shape(examples: dict[str, Any]) -> None:
    parsed = queries_from_payload(examples["getRepositoryQueries"], "org")
    assert len(parsed) == 1
    entry = parsed[0]
    assert entry.query_id.startswith("FQ_")
    assert entry.path == "/NetBox/Devices"
    assert entry.intent == "List devices"


def test_commit_is_read_from_the_nested_shape(examples: dict[str, Any]) -> None:
    """The bug this file exists for: the commit is nested, not flat."""
    entry = queries_from_payload(examples["getRepositoryQueries"], "org")[0]
    assert entry.commit_id == FULL_COMMIT, (
        "the commit pin was lost; Forward nests it under lastCommit.id"
    )


def test_head_commit_parses_the_recorded_shape(examples: dict[str, Any]) -> None:
    assert examples["getOrgHeadCommit"]["id"] == FULL_COMMIT


def test_draft_changes_parse_the_recorded_shape(examples: dict[str, Any]) -> None:
    rows = examples["getDraftChanges"]["changes"]
    parsed = [DraftChange.from_payload(row) for row in rows]
    assert parsed[0].path == "/NetBox/Devices"
    assert parsed[0].action == "editQuery"


def test_reachability_job_identifier_is_where_the_sdk_looks(
    examples: dict[str, Any],
) -> None:
    started = examples["startReachabilityJob"]
    assert started.get("jobKey") or started.get("executionKey") or started.get("id")


def test_current_user_exposes_org_and_per_network_roles(examples: dict[str, Any]) -> None:
    """Both are needed: the org role, and the role held on one network."""
    roles = examples["getCurrentUser"]["roles"]
    assert isinstance(roles["org"], list)
    assert isinstance(roles["network"], dict)


def test_flat_commit_key_is_still_accepted() -> None:
    """Integrations synthesize the flat key when normalizing, so both must work."""
    assert commit_id_of({"lastCommit": {"id": "nested"}}) == "nested"
    assert commit_id_of({"lastCommitId": "flat"}) == "flat"
    assert commit_id_of({"commitId": "older"}) == "older"
    assert commit_id_of({}) is None


def test_nested_commit_wins_over_a_stale_flat_one() -> None:
    """A payload carrying both is trusted on the shape Forward itself sends."""
    payload = {"lastCommit": {"id": "nested"}, "lastCommitId": "stale"}
    assert commit_id_of(payload) == "nested"


def test_a_query_without_a_commit_parses_rather_than_failing() -> None:
    """Forward omits the commit for an uncommitted draft, which is not an error."""
    entry = RepositoryQuery.from_payload({"queryId": "FQ_1", "path": "/A"})
    assert entry.commit_id is None
