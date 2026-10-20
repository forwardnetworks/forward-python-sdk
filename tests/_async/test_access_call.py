"""``client.access()``: one read of the current user, then answers from the roles."""

from __future__ import annotations

import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import ForwardAuthError
from forward_sdk.rbac import RoleTable
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder, network_id: str | None = None) -> AsyncForwardClient:
    return AsyncForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id=network_id,
        transport=recorder.transport,
    )


async def test_the_answer_comes_from_the_roles_forward_reports(
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
    async with make_client(recorder) as client:
        access = await client.access("5", table=role_table)
        assert access.can("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION") is True
        assert access.can("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION", "6") is False
    assert recorder.paths == ["/api/users/current"]


async def test_the_clients_default_network_is_used(
    recorder: Recorder, role_table: RoleTable
) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    async with make_client(recorder, network_id="7") as client:
        access = await client.access(table=role_table)
    assert access.can("NetworkOperation.EDIT_NETWORK_SETTINGS") is True


async def test_the_roles_are_read_once_per_client(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    async with make_client(recorder, network_id="7") as client:
        await client.access()
        await client.access("7")
        await client.access()
    assert recorder.paths == ["/api/users/current"]


async def test_refresh_reads_them_again(recorder: Recorder, role_table: RoleTable) -> None:
    recorder.add(
        "GET",
        "/api/users/current",
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "OPERATOR"}}}),
        json_response({"user": {"id": "1"}, "roles": {"org": [], "network": {"7": "ADMIN"}}}),
    )
    async with make_client(recorder, network_id="7") as client:
        before = await client.access(table=role_table)
        after = await client.access(refresh=True, table=role_table)
    op = "NetworkOperation.EDIT_NETWORK_SETTINGS"
    assert (before.can(op), after.can(op)) == (False, True)


@pytest.mark.parametrize("status", [403, 404])
async def test_a_refusal_to_read_the_roles_is_unknown_not_an_error(
    recorder: Recorder, status: int, role_table: RoleTable
) -> None:
    recorder.add(
        "GET", "/api/users/current", error_response(status, "no", url="/api/users/current")
    )
    async with make_client(recorder, network_id="7") as client:
        access = await client.access(table=role_table)
        decision = access.may("NetworkOperation.EDIT_NETWORK_SETTINGS")
    assert decision.allowed is None
    assert str(status) in decision.reason


async def test_bad_credentials_still_raise(recorder: Recorder) -> None:
    recorder.add("GET", "/api/users/current", error_response(401, "Bad credentials"))
    async with make_client(recorder, network_id="7") as client:
        with pytest.raises(ForwardAuthError):
            await client.access()


async def test_an_impersonated_session_is_unknown(
    recorder: Recorder, role_table: RoleTable
) -> None:
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
    async with make_client(recorder, network_id="7") as client:
        access = await client.access(table=role_table)
    assert access.may("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION").allowed is None
