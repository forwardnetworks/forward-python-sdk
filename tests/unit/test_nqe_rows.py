"""``forward_sdk.nqe.rows``: decoding NQE rows into types, and diffing two result sets by key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from forward_sdk import ForwardClient
from forward_sdk.nqe import decode_rows, diff_rows, flatten_cells, strip_enum_text


def test_enum_text_loses_its_type_prefix_at_any_depth() -> None:
    value = {"v": "Vendor.CISCO", "l": ["OperStatus.UP", "plain.text", "Not.an_enum"], "n": 3}
    assert strip_enum_text(value) == {
        "v": "CISCO",
        "l": ["UP", "plain.text", "Not.an_enum"],
        "n": 3,
    }


class Device(BaseModel):
    name: str
    vendor: Literal["CISCO", "ARISTA"]


def test_rows_decode_into_a_model_with_enums_normalised() -> None:
    rows = [{"name": "r1", "vendor": "Vendor.CISCO"}, {"name": "r2", "vendor": "Vendor.ARISTA"}]
    assert decode_rows(rows, Device, normalize_enums=True) == [
        Device(name="r1", vendor="CISCO"),
        Device(name="r2", vendor="ARISTA"),
    ]


def test_rows_decode_into_a_dataclass() -> None:
    @dataclass
    class Row:
        name: str

    assert decode_rows([{"name": "r1"}], Row) == [Row(name="r1")]


def test_a_row_that_does_not_fit_names_its_index() -> None:
    with pytest.raises(ValueError, match="NQE row 1"):
        decode_rows([{"name": "r1", "vendor": "CISCO"}, {"name": "r2"}], Device)


def test_without_normalising_the_dotted_text_does_not_fit() -> None:
    with pytest.raises(ValueError):
        decode_rows([{"name": "r1", "vendor": "Vendor.CISCO"}], Device)


BEFORE = [
    {"device": "r1", "vrf": "default", "routes": 10},
    {"device": "r2", "vrf": "default", "routes": 5},
    {"device": "r3", "vrf": "default", "routes": 1},
]
AFTER = [
    {"vrf": "default", "routes": 10, "device": "r1"},  # same values, other column order
    {"device": "r2", "vrf": "default", "routes": 6},
    {"device": "r4", "vrf": "default", "routes": 2},
]


def test_added_deleted_and_modified_rows_in_key_order() -> None:
    entries = diff_rows(BEFORE, AFTER, ["device", "vrf"])
    assert [(e.type, (e.before or e.after or {})["device"]) for e in entries] == [
        ("MODIFIED", "r2"),
        ("DELETED", "r3"),
        ("ADDED", "r4"),
    ]
    modified = entries[0]
    assert modified.before == BEFORE[1] and modified.after == AFTER[1]
    assert entries[1].after is None and entries[2].before is None


def test_a_repeated_key_is_refused() -> None:
    with pytest.raises(ValueError, match="not unique"):
        diff_rows([*BEFORE, BEFORE[0]], AFTER, ["device"])


def test_a_missing_key_column_is_refused() -> None:
    with pytest.raises(ValueError, match="no 'site' column"):
        diff_rows(BEFORE, AFTER, ["site"])


@pytest.mark.parametrize("keys", [[], "device"])
def test_key_columns_must_be_a_non_empty_list(keys: Any) -> None:
    with pytest.raises(ValueError, match="key column"):
        diff_rows(BEFORE, AFTER, keys)


def test_diff_source_runs_the_query_on_both_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_query(query: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.append(kwargs)
        return BEFORE if kwargs["snapshot_id"] == "s1" else AFTER

    client = ForwardClient("https://forward.test", username="u", password="p", network_id="1")
    monkeypatch.setattr(client.nqe, "query", fake_query)
    entries = client.nqe.diff_source(
        "foreach d in network.devices select {name: d.name}",
        before="s1",
        after="s2",
        key_columns=["device"],
        network_id="7",
        page_size=50,
    )

    assert [call["snapshot_id"] for call in seen] == ["s1", "s2"]
    assert all(call["network_id"] == "7" and call["page_size"] == 50 for call in seen)
    assert [e.type for e in entries] == ["MODIFIED", "DELETED", "ADDED"]
    client.close()


def test_typed_cells_flatten_to_plain_values() -> None:
    row = {
        "flag": {"value": False},
        "name": {"value": "edge-1", "metadata": {"type": "DEVICE"}},
        "os": {"type": "OS", "alternative": {"value": "NXOS"}},
        "ips": [{"value": "192.0.2.1"}, {"value": "192.0.2.2"}],
        "empty": [],
        "missing": None,
        "record": {"a": {"value": 1}, "b": [{"value": 2}]},
    }
    assert flatten_cells(row) == {
        "flag": False,
        "name": "edge-1",
        "os": "NXOS",
        "ips": ["192.0.2.1", "192.0.2.2"],
        "empty": [],
        "missing": None,
        "record": {"a": 1, "b": [2]},
    }


def test_a_column_named_value_stays_a_column() -> None:
    assert flatten_cells({"value": {"value": "edge-1"}}) == {"value": "edge-1"}
    assert flatten_cells({"value": {"value": 1}, "metadata": {"value": "m"}}) == {
        "value": 1,
        "metadata": "m",
    }
    assert flatten_cells({"type": {"value": "t"}, "alternative": {"value": "a"}}) == {
        "type": "t",
        "alternative": "a",
    }


def test_numbers_compare_by_type_and_value() -> None:
    # Unlike Go, which decodes both to float64, 1 and 1.0 differ.
    entries = diff_rows([{"k": "a", "n": 1}], [{"k": "a", "n": 1.0}], ["k"])
    assert [e.type for e in entries] == ["MODIFIED"]
    # Keys keep their type too: the number 1 and the text "1" are different rows.
    entries = diff_rows([{"k": 1}], [{"k": "1"}], ["k"])
    assert sorted(str(e.type and e.type.value) for e in entries) == ["ADDED", "DELETED"]


def test_diff_source_refuses_a_blank_snapshot() -> None:
    client = ForwardClient("https://forward.test", username="u", password="p", network_id="1")
    with pytest.raises(ValueError, match="both snapshot ids"):
        client.nqe.diff_source("q", before=" ", after="s2", key_columns=["k"])
    client.close()
