# Generated from tests/_async/test_access_call.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""``client.access()``: one read of the current user, then answers from the roles."""

from __future__ import annotations

import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardAuthError
from forward_sdk.rbac import RoleTable
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder, network_id: str | None = None) -> ForwardClient:
    return ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id=network_id,
        transport=recorder.transport,
    )


def test_the_answer_comes_from_the_roles_forward_reports(
    recorder: Recorder, role_table: RoleTable
) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response(
            {
                "user": {"id": "1", "username": "nb"},
                "roles": {"org": [], "network": {"5": "WORKSPACE_OPERATOR", "6": "OPERATOR"}},
            }
        ),
    )
    with make_client(recorder) as client:
        access = client.access("5", table=role_table)
        assert access.can("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION") is True
        assert access.can("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION", "6") is False
    assert recorder.paths == ["/api/users/current"]


def test_the_clients_default_network_is_used(recorder: Recorder, role_table: RoleTable) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    with make_client(recorder, network_id="7") as client:
        access = client.access(table=role_table)
    assert access.can("NetworkOperation.EDIT_NETWORK_SETTINGS") is True


def test_the_roles_are_read_once_per_client(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    with make_client(recorder, network_id="7") as client:
        client.access()
        client.access("7")
        client.access()
    assert recorder.paths == ["/api/users/current"]


def test_refresh_reads_them_again(recorder: Recorder, role_table: RoleTable) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "OPERATOR"}}}),
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    with make_client(recorder, network_id="7") as client:
        before = client.access(table=role_table)
        after = client.access(refresh=True, table=role_table)
    op = "NetworkOperation.EDIT_NETWORK_SETTINGS"
    assert (before.can(op), after.can(op)) == (False, True)


@pytest.mark.parametrize("status", [403, 404])
def test_a_refusal_to_read_the_roles_is_unknown_not_an_error(
    recorder: Recorder, status: int, role_table: RoleTable
) -> None:
    recorder.add(
        "GET", "/api/users/current", error_response(status, "no", url="/api/users/current")
    )
    with make_client(recorder, network_id="7") as client:
        access = client.access(table=role_table)
        decision = access.may("NetworkOperation.EDIT_NETWORK_SETTINGS")
    assert decision.allowed is None
    assert str(status) in decision.reason


def test_bad_credentials_still_raise(recorder: Recorder) -> None:
    recorder.add("GET", "/api/users/current", error_response(401, "Bad credentials"))
    with make_client(recorder, network_id="7") as client:
        with pytest.raises(ForwardAuthError):
            client.access()


def test_an_impersonated_session_is_unknown(recorder: Recorder, role_table: RoleTable) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response(
            {
                "user": {"id": "1"},
                "roles": {"org": [], "network": {"7": "OPERATOR"}},
                "impersonator": "support-1",
            }
        ),
    )
    with make_client(recorder, network_id="7") as client:
        access = client.access(table=role_table)
    assert access.may("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION").allowed is None
