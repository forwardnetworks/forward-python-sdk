"""Every operation in the spec is implemented exactly once, and correctly.

This is the drift alarm. After ``scripts/sync_spec.py`` pulls a newer Forward
description, these tests name precisely which operations appeared, vanished, or
changed shape, so a spec update cannot quietly leave the SDK behind.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import forward_sdk._ops as ops_package
from forward_sdk import AsyncForwardClient, ForwardClient
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._ops import REGISTRY
from forward_sdk._ops._generic import snake

ALLOWLIST_PATH = Path("spec/coverage-allowlist.yaml")
GATING_PATH = Path("spec/gating.yaml")


def _import_all_builders() -> None:
    """Import every builder module so the registry is fully populated."""
    for module in pkgutil.iter_modules(ops_package.__path__):
        importlib.import_module(f"{ops_package.__name__}.{module.name}")


_import_all_builders()


@pytest.fixture(scope="module")
def allowlist() -> dict[str, Any]:
    return yaml.safe_load(ALLOWLIST_PATH.read_text()) or {}


@pytest.fixture(scope="module")
def unimplemented(allowlist: dict[str, Any]) -> set[str]:
    skip = set((allowlist.get("skip") or {}).keys())
    pending = set((allowlist.get("pending") or {}).get("operation_ids") or [])
    return skip | pending


def test_every_operation_is_implemented_or_listed(unimplemented: set[str]) -> None:
    missing = sorted(set(OPERATIONS) - set(REGISTRY) - unimplemented)
    assert not missing, (
        f"{len(missing)} operation(s) have no request builder. Implement them, or "
        f"list them under `pending` in {ALLOWLIST_PATH}:\n  " + "\n  ".join(missing)
    )


def test_no_builder_implements_an_unknown_operation() -> None:
    """A builder for an id the spec does not define is a typo or a stale endpoint."""
    unknown = sorted(set(REGISTRY) - set(OPERATIONS))
    assert not unknown, f"builders reference unknown operationIds: {unknown}"


def test_allowlisted_operations_are_not_also_implemented(unimplemented: set[str]) -> None:
    both = sorted(unimplemented & set(REGISTRY))
    assert not both, (
        f"these are implemented but still listed as unimplemented in {ALLOWLIST_PATH}: {both}"
    )


def test_allowlist_entries_still_exist_in_the_spec(unimplemented: set[str]) -> None:
    stale = sorted(unimplemented - set(OPERATIONS))
    assert not stale, f"{ALLOWLIST_PATH} names operations the spec no longer has: {stale}"


def test_skipped_operations_give_a_reason(allowlist: dict[str, Any]) -> None:
    for operation_id, entry in (allowlist.get("skip") or {}).items():
        assert entry and str(entry).strip(), f"{operation_id} is skipped without a reason"


@pytest.mark.parametrize("operation_id", sorted(REGISTRY))
def test_builder_matches_the_spec(operation_id: str) -> None:
    """A builder's method, path and dispatch come from the table, so they cannot drift."""
    operation = OPERATIONS[operation_id]
    builder = REGISTRY[operation_id]
    assert getattr(builder, "__forward_operation_id__", None) == operation_id
    assert operation.method in {"get", "put", "post", "delete", "patch", "head", "options"}
    assert operation.path.startswith("/")
    assert "?" not in operation.path


def test_client_exposes_every_api_group() -> None:
    """Each group in the description is reachable from the client."""

    hand_written = {
        "Network Management": "networks",
        "Network Snapshots": "snapshots",
        "Network Devices": "devices",
        "Device Tags": "device_tags",
        "NQE": "nqe",
        "Current Version": "networks",
        "NQE Repository": "nqe",
        "Snapshot Reachability": "snapshots",
    }
    client = ForwardClient("https://forward.test")
    try:
        for tag in sorted({op.tag for op in OPERATIONS.values()}):
            attribute = hand_written.get(tag) or re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_")
            assert hasattr(client, attribute), f"no client attribute for {tag!r}"
        assert hasattr(client.nqe, "repo")
    finally:
        client.close()


def test_every_operation_has_a_service_method() -> None:
    """No operation is reachable only through a raw request builder."""
    client = ForwardClient("https://forward.test")
    try:
        available: set[str] = set()
        for attribute in dir(client):
            if attribute.startswith("_"):
                continue
            service = getattr(client, attribute, None)
            if service is None or not type(service).__name__.endswith("Service"):
                continue
            available.update(name for name in dir(service) if not name.startswith("_"))
            repo = getattr(service, "repo", None)
            if repo is not None:
                available.update(n for n in dir(repo) if not n.startswith("_"))
    finally:
        client.close()

    # Hand-written services use curated names, so only the generated groups are
    # checked by operation id.
    generated_tags = {
        op.tag
        for op in OPERATIONS.values()
        if op.tag
        not in {
            "Network Management",
            "Network Snapshots",
            "Network Devices",
            "Device Tags",
            "NQE",
            "Current Version",
            "NQE Repository",
            "Snapshot Reachability",
        }
    }
    missing = sorted(
        op.operation_id
        for op in OPERATIONS.values()
        if op.tag in generated_tags and snake(op.operation_id) not in available
    )
    assert not missing, f"operations with no service method: {missing}"


def test_sync_and_async_clients_have_the_same_surface() -> None:
    """The generated sync client must not lag behind its async source."""

    def public(obj: type) -> set[str]:
        return {name for name in dir(obj) if not name.startswith("_")}

    async_only = public(AsyncForwardClient) - public(ForwardClient) - {"aclose"}
    sync_only = public(ForwardClient) - public(AsyncForwardClient) - {"close"}
    assert not async_only, f"async client has extra members: {sorted(async_only)}"
    assert not sync_only, f"sync client has extra members: {sorted(sync_only)}"


def test_deprecated_operations_are_marked() -> None:
    """The seven operations Forward deprecated are recorded as such."""
    deprecated = {op_id for op_id, op in OPERATIONS.items() if op.deprecated}
    assert "getLatestProcessedSnapshot" in deprecated
    assert len(deprecated) == 7


def test_unpublished_operations_are_declared() -> None:
    """Anything outside the published description is marked unpublished."""
    unpublished = {op_id for op_id, op in OPERATIONS.items() if op.stability == "unpublished"}
    assert "commitDraftChanges" in unpublished
    assert "startReachabilityJob" in unpublished
    published_spec = yaml.safe_load(Path("spec/forward-openapi.yaml").read_text())
    published_ids = {
        operation["operationId"]
        for item in published_spec["paths"].values()
        for key, operation in item.items()
        if isinstance(operation, dict) and key != "parameters"
    }
    assert not (unpublished & published_ids), (
        "an operation marked unpublished is in fact published; remove it from spec/unpublished.yaml"
    )


def test_gated_tags_exist_in_the_spec() -> None:
    """Gating hints must name real tags, or the documentation drifts silently."""
    gating = (yaml.safe_load(GATING_PATH.read_text()) or {}).get("tags", {}) or {}
    spec_tags = {op.tag for op in OPERATIONS.values()}
    unknown = sorted(set(gating) - spec_tags)
    assert not unknown, f"{GATING_PATH} names tags that do not exist: {unknown}"


def test_gating_hints_reach_the_operations() -> None:
    assert OPERATIONS["getVulnerabilities"].gating == ("license",)
    assert OPERATIONS["getNetworks"].gating == ()
