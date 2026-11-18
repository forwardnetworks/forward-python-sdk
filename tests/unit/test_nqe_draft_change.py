"""``DraftChange``: Forward's ChangeInfo, where only an addition carries a path of its own."""

from __future__ import annotations

from typing import Any

import pytest

from forward_sdk.nqe.repository import DraftChange

BASIS = {"queryId": "Q_1", "commitId": "c1", "path": "/Team/Devices"}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "QUERY_ADD", "path": "/Team/New"}, "/Team/New"),
        ({"type": "QUERY_EDIT", "sourceCodeSha": "abc", "basis": BASIS}, "/Team/Devices"),
        ({"type": "QUERY_EDIT", "name": "Renamed", "basis": BASIS}, "/Team/Renamed"),
        ({"type": "QUERY_EDIT", "directory": "/Other", "basis": BASIS}, "/Other/Devices"),
        ({"type": "QUERY_EDIT", "directory": "/Other/", "name": "N", "basis": BASIS}, "/Other/N"),
        ({"type": "QUERY_DELETE", "basis": BASIS}, "/Team/Devices"),
        ({"type": "DIR_ADD", "path": "/Team/"}, "/Team/"),
        ({"type": "PERMISSION_SET", "directory": "/Team/"}, ""),
    ],
)
def test_query_path(payload: dict[str, Any], expected: str) -> None:
    assert DraftChange.from_payload(payload).query_path == expected


def test_the_fields_and_the_older_action_spelling() -> None:
    edit = DraftChange.from_payload({"type": "QUERY_EDIT", "name": "N", "basis": BASIS})
    assert edit.path == "" and edit.type == "QUERY_EDIT" and edit.action == "QUERY_EDIT"
    assert (edit.basis_query_id, edit.basis_commit_id, edit.basis_path) == (
        "Q_1",
        "c1",
        "/Team/Devices",
    )
    staged = DraftChange.from_payload({"path": "/Team/Q", "action": "editQuery"})
    assert staged.action == "editQuery" and staged.type is None


def test_to_api_round_trips() -> None:
    payload = {"path": "", "type": "QUERY_EDIT", "name": "N", "basis": BASIS}
    assert DraftChange.from_payload(payload).to_api() == {**payload, "action": "QUERY_EDIT"}


def test_touches_names_every_path_a_commit_would_publish_it_through() -> None:
    rename = DraftChange.from_payload({"type": "QUERY_EDIT", "name": "New", "basis": BASIS})
    assert rename.touches("/Team/New")  # where the rename leaves the query
    assert rename.touches("/Team/Devices")  # the committed path it moves away from
    assert not rename.touches("/Team/Other")
    assert not rename.touches("")
    add = DraftChange.from_payload({"type": "QUERY_ADD", "path": "/Team/Q"})
    assert add.touches("/Team/Q") and not add.touches("/Team/Devices")
