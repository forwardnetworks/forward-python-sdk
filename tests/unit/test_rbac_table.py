"""Reading Forward's access package, on the shapes the reader relies on."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forward_sdk import rbac, rbac_dump
from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.rbac import ACCESS_DIR, RoleTable
from tests.unit.fake_forward import NETWORK_ROLE, write_checkout


def test_a_role_holds_what_the_roles_below_it_hold(role_table: RoleTable) -> None:
    lowest = {n: r.lowest_network_role for n, r in role_table.network_operations.items()}
    assert lowest == {
        "VIEW_NETWORK_AND_SNAPSHOTS": "LIMITED_READ_ONLY",
        "USE_NQE": "READ_ONLY",
        "CREATE_WORKSPACE": "OPERATOR",
        "MANAGE_CHANGE_SETS": "OPERATOR",
        "TRIGGER_SNAPSHOT_COLLECTION": "WORKSPACE_OPERATOR",
        "EDIT_NETWORK_SETTINGS": "ADMIN",
    }


def test_org_operations_are_placed_by_the_roles_that_allow_them(role_table: RoleTable) -> None:
    lowest = {n: r.lowest_network_role for n, r in role_table.org_operations.items()}
    assert lowest == {
        "VIEW_DATA_FILES": "LIMITED_READ_ONLY",
        "MANAGE_DATA_FILES": "ADMIN",
        "MANAGE_USER_ACCOUNTS": None,
    }


def test_an_operation_carries_its_access_type_facet_and_doc(role_table: RoleTable) -> None:
    row = role_table.network_operations["MANAGE_CHANGE_SETS"]
    assert (row.access_type, row.license_facet, row.doc) == (
        "WRITE",
        "PREDICT",
        "Manage change sets",
    )


def test_the_json_copy_reads_back_the_same(role_table: RoleTable, tmp_path: Path) -> None:
    file = tmp_path / "table.json"
    file.write_text(role_table.to_json())
    again = RoleTable.from_json(file)
    assert again.network_operations == role_table.network_operations
    assert again.org_operations == role_table.org_operations


def test_a_role_missing_from_the_source_stops_the_reader(tmp_path: Path) -> None:
    root = write_checkout(tmp_path / "fwd")
    role_file = root / ACCESS_DIR / "NetworkRole.java"
    role_file.write_text(NETWORK_ROLE.replace("WORKSPACE_OPERATOR(", "RENAMED("))
    with pytest.raises(ForwardConfigurationError, match="no constant"):
        RoleTable.from_checkout(root)


def test_a_role_naming_an_operation_that_does_not_exist_stops_the_reader(tmp_path: Path) -> None:
    root = write_checkout(tmp_path / "fwd")
    role_file = root / ACCESS_DIR / "NetworkRole.java"
    role_file.write_text(NETWORK_ROLE.replace("USE_NQE", "USE_NOTHING"))
    with pytest.raises(ForwardConfigurationError, match="do not exist"):
        RoleTable.from_checkout(root)


@pytest.mark.parametrize(
    "form",
    [
        "OPERATOR.allowedOperations.stream().filter(x -> true)",
        "Sets.difference(OPERATOR.allowedOperations, EnumSet.of())",
        "NetworkOperation.VALUES",
        "EnumSet.allOf(NetworkOperation.class)",
    ],
)
def test_a_form_the_reader_does_not_follow_stops_it(tmp_path: Path, form: str) -> None:
    """Reading past one would give a role the wrong operations and so wrong answers."""
    root = write_checkout(tmp_path / "fwd")
    role_file = root / ACCESS_DIR / "NetworkRole.java"
    role_file.write_text(
        NETWORK_ROLE.replace(
            "EnumSet.of(NetworkOperation.USE_NQE)", f"EnumSet.of(NetworkOperation.USE_NQE), {form}"
        )
    )
    with pytest.raises(ForwardConfigurationError, match="does not follow"):
        RoleTable.from_checkout(root)


def test_a_directory_that_is_not_a_checkout_is_said_so(tmp_path: Path) -> None:
    with pytest.raises(ForwardConfigurationError, match="does not look like"):
        RoleTable.from_checkout(tmp_path)


class TestDiscovery:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FORWARD_RBAC_TABLE", raising=False)
        monkeypatch.delenv("FORWARD_SOURCE", raising=False)
        monkeypatch.setattr(rbac, "_discovered", {})

    def test_nothing_set_means_no_table(self) -> None:
        assert rbac.discover() is None

    def test_a_checkout_is_found_through_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FORWARD_SOURCE", str(write_checkout(tmp_path / "fwd")))
        table = rbac.discover()
        assert table is not None
        assert "TRIGGER_SNAPSHOT_COLLECTION" in table.network_operations

    def test_the_json_copy_wins_over_a_checkout(
        self, role_table: RoleTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file = tmp_path / "t.json"
        raw = json.loads(role_table.to_json())
        raw["source"] = "from json"
        file.write_text(json.dumps(raw))
        monkeypatch.setenv("FORWARD_RBAC_TABLE", str(file))
        monkeypatch.setenv("FORWARD_SOURCE", str(tmp_path / "nowhere"))
        table = rbac.discover()
        assert table is not None
        assert table.source == "from json"

    def test_a_path_that_is_set_but_wrong_is_an_error_not_no_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FORWARD_SOURCE", str(tmp_path / "nowhere"))
        with pytest.raises(ForwardConfigurationError):
            rbac.discover()


FWD = Path(os.environ.get("FORWARD_SOURCE", "~/src/fwd")).expanduser()


@pytest.mark.skipif(not (FWD / ACCESS_DIR).is_dir(), reason="no Forward checkout")
def test_the_reader_understands_the_real_source() -> None:
    """Skipped without a checkout. Fails when Forward changes the shape the reader relies on."""
    table = RoleTable.from_checkout(FWD)
    assert len(table.network_operations) > 50
    assert len(table.org_operations) > 30
    collect = table.network_operations["TRIGGER_SNAPSHOT_COLLECTION"]
    assert collect.lowest_network_role == "WORKSPACE_OPERATOR"
    assert table.org_operations["MANAGE_USER_ACCOUNTS"].lowest_network_role is None
    assert not set(table.network_operations) & set(table.org_operations)


def test_the_dump_command_writes_a_table_that_reads_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_checkout(tmp_path / "fwd")
    assert rbac_dump.main(["--fwd", str(root)]) == 0
    file = tmp_path / "t.json"
    file.write_text(capsys.readouterr().out)
    assert "TRIGGER_SNAPSHOT_COLLECTION" in RoleTable.from_json(file).network_operations
