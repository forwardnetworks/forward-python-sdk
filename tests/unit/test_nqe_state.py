"""Typed NQE state reads: the queries, the row types and the client methods."""

from __future__ import annotations

from typing import Any

import pytest

from forward_sdk import ForwardClient
from forward_sdk.nqe.state import (
    ACL_ENTRIES_QUERY,
    BGP_SESSIONS_QUERY,
    INTERFACE_ADMIN_QUERY,
    AclEntry,
    BgpSession,
    InterfaceAdmin,
    lldp_query,
)


def test_a_32_bit_as_reads_as_text_whether_sent_as_a_number_or_text() -> None:
    row = {
        "Device": "r1",
        "VRF": "default",
        "NeighborIP": "192.0.2.1",
        "SessionState": "ESTABLISHED",
    }
    assert BgpSession.model_validate({**row, "RemoteAS": 4200000000}).remote_as == "4200000000"
    assert BgpSession.model_validate({**row, "RemoteAS": "65001"}).remote_as == "65001"
    session = BgpSession.model_validate({**row, "RemoteAS": None})
    assert session.remote_as is None
    assert session.established and session.key == ("r1", "default", "192.0.2.1")


def test_interface_up_is_the_enum_text_not_up() -> None:
    # Forward writes AdminStatus.UP (seen live); comparing with "UP" would match nothing.
    up = InterfaceAdmin.model_validate(
        {"Device": "r1", "Interface": "e1", "AdminStatus": "AdminStatus.UP"}
    )
    bare = InterfaceAdmin.model_validate({"Device": "r1", "Interface": "e1", "AdminStatus": "UP"})
    assert up.up and not bare.up


def test_lldp_query_scopes_devices_and_skips_interfaces() -> None:
    query = lldp_query(["core-1", "core-2"], ["ma0"])
    assert 'where device.name == "core-1" || device.name == "core-2"' in query
    assert 'where interface.name != "ma0"' in query
    assert query.index("foreach device") < query.index("core-1") < query.index("foreach interface")
    assert query.index("foreach interface") < query.index("ma0") < query.index("foreach neighbor")
    assert "where" not in lldp_query()


@pytest.mark.parametrize("name", ['a"b', "a\\b", "a\nb", ""])
def test_a_name_that_would_break_the_literal_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="string literal"):
        lldp_query([name])


def test_a_bare_string_is_refused() -> None:
    with pytest.raises(ValueError, match="list of names"):
        lldp_query("core-1")


def make(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]
) -> tuple[ForwardClient, list[Any]]:
    calls: list[Any] = []

    def fake_query(query: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append((query, kwargs))
        return rows

    client = ForwardClient("https://forward.test", username="u", password="p", network_id="1")
    monkeypatch.setattr(client.nqe, "query", fake_query)
    return client, calls


def test_each_read_runs_its_query_on_the_snapshot_given(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = make(monkeypatch, [])
    client.nqe.bgp_sessions(snapshot_id="s1")
    client.nqe.interface_admin(snapshot_id="s1", network_id="7")
    client.nqe.acl_entries(snapshot_id="s1")
    client.nqe.lldp_adjacencies(snapshot_id="s1", devices=["core-1"])
    assert [q for q, _ in calls[:3]] == [
        BGP_SESSIONS_QUERY,
        INTERFACE_ADMIN_QUERY,
        ACL_ENTRIES_QUERY,
    ]
    assert "core-1" in calls[3][0]
    assert all(kw["snapshot_id"] == "s1" for _, kw in calls)
    assert calls[1][1]["network_id"] == "7"
    client.close()


def test_acl_counts_counts_entries_per_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"Device": "r1", "AclName": "edge-in", "Action": "AclAction.PERMIT"},
        {"Device": "r1", "AclName": "edge-in", "Action": "AclAction.DENY"},
        {"Device": "r2", "AclName": "edge-in", "Action": "AclAction.PERMIT"},
    ]
    client, _ = make(monkeypatch, rows)
    assert client.nqe.acl_counts(snapshot_id="s1") == {("r1", "edge-in"): 2, ("r2", "edge-in"): 1}
    client.close()


def test_lldp_query_text_matches_the_go_sdk() -> None:
    # Go v0.8.32 lldpQuery output for the same inputs.
    assert lldp_query(["core-1", "core-2"], ["ma0"]) == (
        "foreach device in network.devices\n"
        'where device.name == "core-1" || device.name == "core-2"\n'
        "foreach interface in device.interfaces\n"
        'where interface.name != "ma0"\n'
        "foreach neighbor in interface.lldp.neighbors\n"
        "select {\n"
        "  Device: device.name, LocalIntf: interface.name,\n"
        "  RemoteSys: neighbor.deviceName, RemotePort: neighbor.portName\n"
        "}"
    )
    assert lldp_query() == (
        "foreach device in network.devices\n"
        "foreach interface in device.interfaces\n"
        "foreach neighbor in interface.lldp.neighbors\n"
        "select {\n"
        "  Device: device.name, LocalIntf: interface.name,\n"
        "  RemoteSys: neighbor.deviceName, RemotePort: neighbor.portName\n"
        "}"
    )


def test_a_null_name_reads_as_empty_rather_than_failing() -> None:
    entry = AclEntry.model_validate({"Device": "r1", "AclName": None, "Action": None})
    assert entry.acl == ("r1", "") and entry.action is None


def test_established_ignores_case() -> None:
    row = {"Device": "r1", "VRF": "default", "NeighborIP": "192.0.2.1"}
    assert BgpSession.model_validate({**row, "SessionState": "Established"}).established
    assert not BgpSession.model_validate({**row, "SessionState": "IDLE"}).established


ACL_ROWS = [
    {"Device": "r1", "AclName": "edge-in", "Action": "AclAction.PERMIT", "UUID": "u-1", "Seq": 10},
    {"Device": "r1", "AclName": "edge-in", "Action": "AclAction.DENY", "UUID": "u-2", "Seq": 20},
]


def test_acl_entries_with_keeps_the_row_and_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = make(monkeypatch, ACL_ROWS)
    entries = client.nqe.acl_entries_with(
        query="custom", key_columns=["Device", "UUID", "Seq"], snapshot_id="s1"
    )
    assert calls[0][0] == "custom"
    assert [e.identity for e in entries] == [("r1", "u-1", "10"), ("r1", "u-2", "20")]
    assert entries[0].row["Seq"] == 10 and entries[0].uuid == "u-1"
    assert entries[1].key == ("r1", "edge-in", "u-2")
    assert "row" not in entries[0].model_dump()
    assert len({*entries}) == 2  # still hashable, for set-based before/after diffs
    client.close()


def test_acl_entries_with_refuses_a_repeated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = make(monkeypatch, ACL_ROWS)
    with pytest.raises(ValueError, match="not unique"):
        client.nqe.acl_entries_with(query="custom", key_columns=["Device", "AclName"])
    client.close()


def test_acl_entries_with_needs_a_query_for_key_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = make(monkeypatch, ACL_ROWS)
    with pytest.raises(ValueError, match="give a query"):
        client.nqe.acl_entries_with(key_columns=["UUID"])
    entries = client.nqe.acl_entries_with()
    assert calls[-1][0] == ACL_ENTRIES_QUERY and entries[0].identity == ()
    client.close()


def test_acl_entries_with_names_a_missing_key_column(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = make(monkeypatch, ACL_ROWS)
    with pytest.raises(ValueError, match="'Rule'"):
        client.nqe.acl_entries_with(query="custom", key_columns=["Rule"])
    client.close()


def test_entries_from_the_plain_read_are_hashable(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = make(monkeypatch, ACL_ROWS)
    entries = client.nqe.acl_entries()
    assert len(set(entries)) == 2 and entries[0].row == {} and entries[0].identity == ()
    client.close()


def test_a_column_named_row_or_identity_does_not_clash(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"Device": "r1", "AclName": "a", "Action": "x", "row": 5, "identity": "text"}]
    client, _ = make(monkeypatch, rows)
    entry = client.nqe.acl_entries_with(query="custom", key_columns=["identity"])[0]
    assert entry.identity == ("text",) and entry.row["row"] == 5
    client.close()


def test_an_empty_query_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = make(monkeypatch, ACL_ROWS)
    client.nqe.acl_entries_with(query="")
    assert calls[-1][0] == ACL_ENTRIES_QUERY
    client.close()
