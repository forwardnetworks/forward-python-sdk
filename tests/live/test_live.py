"""Tests against a real Forward instance.

Skipped unless credentials are present, so an ordinary ``pytest`` run never
needs a server. To run them::

    export FORWARD_URL=https://fwd.app
    export FORWARD_USERNAME=your-token-access-key
    export FORWARD_PASSWORD=your-token-secret
    export FORWARD_NETWORK_ID=101
    uv run pytest -m live

Everything here is read-only. Tests that write are additionally gated on
``FORWARD_LIVE_WRITES=1``, and confine themselves to the calling user's own
draft area, which is discarded afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from forward_sdk import ForwardClient, ForwardNotFoundError, QueryRef

pytestmark = pytest.mark.live

REQUIRED = ("FORWARD_URL", "FORWARD_USERNAME", "FORWARD_PASSWORD", "FORWARD_NETWORK_ID")

TRIVIAL_QUERY = "foreach device in network.devices select {name: device.name}"


def _missing() -> list[str]:
    return [name for name in REQUIRED if not os.environ.get(name)]


@pytest.fixture(scope="module")
def client() -> Iterator[ForwardClient]:
    missing = _missing()
    if missing:
        pytest.skip(f"live tests need {', '.join(missing)}")
    with ForwardClient.from_env(user_agent="forward-sdk-live-tests/1") as connected:
        yield connected


@pytest.fixture(scope="module")
def network_id() -> str:
    return os.environ["FORWARD_NETWORK_ID"]


def test_reports_the_instance_version(client: ForwardClient) -> None:
    version = client.version()
    assert version.get("version")


def test_lists_networks(client: ForwardClient, network_id: str) -> None:
    networks = client.networks.list()
    assert networks
    assert any(network.id == network_id for network in networks), (
        f"FORWARD_NETWORK_ID {network_id} is not among the visible networks"
    )


def test_finds_a_processed_snapshot(client: ForwardClient, network_id: str) -> None:
    snapshot = client.snapshots.latest_processed(network_id)
    if snapshot is None:
        pytest.skip(f"network {network_id} has no processed snapshot")
    assert snapshot.id
    assert str(snapshot.state) == "PROCESSED"


def test_snapshot_metrics(client: ForwardClient, network_id: str) -> None:
    snapshot = client.snapshots.latest_processed(network_id)
    if snapshot is None or not snapshot.id:
        pytest.skip("no processed snapshot")
    assert isinstance(client.snapshots.metrics(snapshot.id), dict)


def test_lists_devices(client: ForwardClient, network_id: str) -> None:
    devices = client.devices.list(network_id, limit=5)
    assert all(device.name for device in devices)


def test_runs_a_query_synchronously(client: ForwardClient, network_id: str) -> None:
    result = client.nqe.run(TRIVIAL_QUERY, network_id=network_id, limit=5)
    assert result.items is not None
    assert len(result.items) <= 5


def test_runs_a_query_in_the_background(client: ForwardClient, network_id: str) -> None:
    execution = client.nqe.execute(TRIVIAL_QUERY, network_id=network_id)
    status = execution.wait(timeout=600)
    assert str(status.outcome) == "OK"

    paged = list(execution.rows(page_size=100))
    streamed = list(execution.stream())
    # The two ways of reading a result must agree.
    assert len(paged) == len(streamed)
    if paged:
        assert set(paged[0]) == set(streamed[0])


def test_query_library_is_readable(client: ForwardClient) -> None:
    queries = client.nqe.queries()
    assert isinstance(queries, list)


def test_unknown_query_path_raises_not_found(client: ForwardClient, network_id: str) -> None:
    with pytest.raises(ForwardNotFoundError):
        client.nqe.execute(
            QueryRef.by_path("/forward-sdk-live-tests/definitely-not-here"),
            network_id=network_id,
        )


def test_counters_record_the_traffic(client: ForwardClient) -> None:
    before = client.counters.http_attempts
    client.version()
    assert client.counters.http_attempts > before


@pytest.mark.skipif(
    os.environ.get("FORWARD_LIVE_WRITES") != "1",
    reason="writing tests need FORWARD_LIVE_WRITES=1",
)
def test_draft_can_be_staged_and_discarded(client: ForwardClient) -> None:
    """Stage a query draft, confirm it appears, then discard it."""
    path = "/forward-sdk-live-tests/scratch"
    client.nqe.repo.stage_add(path, TRIVIAL_QUERY)
    try:
        assert any(draft.path == path for draft in client.nqe.repo.drafts())
    finally:
        client.nqe.repo.discard(path)

    assert not any(draft.path == path for draft in client.nqe.repo.drafts())
