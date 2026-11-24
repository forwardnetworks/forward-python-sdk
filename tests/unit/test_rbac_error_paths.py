"""The ways reading a role table fails raise ``ForwardConfigurationError``, as they always did."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.rbac import ACCESS_DIR, RoleTable

NETWORK_OPERATIONS = """\
public enum NetworkOperation {
    /// Read a network's devices
    VIEW_DEVICES(AccessType.READ, LicenseFacet.BASE),
    /// Run an NQE query
    RUN_NQE(AccessType.READ, LicenseFacet.BASE);
}
"""
ORG_OPERATIONS = """\
public enum OrgOperation {
    /// List the networks of an org
    LIST_NETWORKS(AccessType.READ, LicenseFacet.BASE);
}
"""
NETWORK_ROLES = """\
public enum NetworkRole {
    LIMITED_READ_ONLY(
        ImmutableSet.of(NetworkOperation.VIEW_DEVICES),
        ImmutableSet.of(OrgOperation.LIST_NETWORKS)),
    READ_ONLY(
        ImmutableSet.<NetworkOperation>builder()
            .addAll(LIMITED_READ_ONLY.allowedOperations)
            .add(NetworkOperation.RUN_NQE)
            .build(),
        LIMITED_READ_ONLY.allowedOrgOperations),
    OPERATOR(READ_ONLY.allowedOperations, READ_ONLY.allowedOrgOperations),
    WORKSPACE_OPERATOR(OPERATOR.allowedOperations, OPERATOR.allowedOrgOperations),
    ADMIN(WORKSPACE_OPERATOR.allowedOperations, WORKSPACE_OPERATOR.allowedOrgOperations);

    public static final ImmutableSet<NetworkRole> VALUES = ImmutableSet.copyOf(values());
}
"""


def _copy_checkout(tmp_path: Path) -> Path:
    """A small Forward source tree: three access files in the place the reader looks."""
    root = tmp_path / "checkout"
    access = root / ACCESS_DIR
    access.mkdir(parents=True)
    (access / "NetworkOperation.java").write_text(NETWORK_OPERATIONS, encoding="utf-8")
    (access / "OrgOperation.java").write_text(ORG_OPERATIONS, encoding="utf-8")
    (access / "NetworkRole.java").write_text(NETWORK_ROLES, encoding="utf-8")
    return root


def test_a_table_file_that_is_not_utf8_is_a_configuration_error(tmp_path: Path) -> None:
    file = tmp_path / "table.json"
    file.write_bytes(b"\xff\xfe\x00 not text")
    with pytest.raises(ForwardConfigurationError, match="is not a role table"):
        RoleTable.from_json(file)


def test_a_json_error_keeps_the_parser_error_as_its_cause(tmp_path: Path) -> None:
    file = tmp_path / "table.json"
    file.write_text("{not json", encoding="utf-8")
    with pytest.raises(ForwardConfigurationError) as caught:
        RoleTable.from_json(file)
    assert isinstance(caught.value.__cause__, ValueError)


def test_an_invalid_checkout_is_refused_before_git_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_checkout(tmp_path)
    roles = root / ACCESS_DIR / "NetworkRole.java"
    roles.write_text(roles.read_text("utf-8").replace("    OPERATOR(", "    OPERATORX(", 1))

    def no_git(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    with pytest.raises(ForwardConfigurationError, match="no constant"):
        RoleTable.from_checkout(root)


def test_a_checkout_without_operations_says_where_it_looked(tmp_path: Path) -> None:
    root = _copy_checkout(tmp_path)
    (root / ACCESS_DIR / "OrgOperation.java").write_text("public enum OrgOperation {}\n")
    with pytest.raises(ForwardConfigurationError) as caught:
        RoleTable.from_checkout(root)
    assert str(caught.value) == f"found no operations under {root / ACCESS_DIR}"
    assert caught.value.__cause__ is None
