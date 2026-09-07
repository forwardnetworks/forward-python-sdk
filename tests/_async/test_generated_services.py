"""The service layer generated from Forward's API description.

Spot-checks representative groups rather than all 169 methods: the shapes are
produced by one generator, so a fault in it shows up everywhere. What the spec
conformance suite cannot check -- that a method reaches the right endpoint, that
network and snapshot defaults are applied, that responses are parsed into models
-- is checked here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import ForwardPermissionError
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder, **overrides: Any) -> AsyncForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "network_id": "101",
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return AsyncForwardClient("https://forward.test", **settings)


async def test_response_is_parsed_into_its_model(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/locations",
        json_response([{"id": "loc-1", "name": "New York", "lat": 40.7, "lng": -74.0}]),
    )
    async with make_client(recorder) as client:
        locations = await client.network_locations.get_locations()

    assert locations[0].name == "New York"
    assert locations[0].id == "loc-1"


async def test_network_defaults_to_the_client_setting(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/locations", json_response([]))
    async with make_client(recorder) as client:
        await client.network_locations.get_locations()

    assert recorder.paths == ["/api/networks/101/locations"]


async def test_explicit_network_wins(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/999/locations", json_response([]))
    async with make_client(recorder) as client:
        await client.network_locations.get_locations(network_id="999")

    assert recorder.paths == ["/api/networks/999/locations"]


async def test_snapshot_omitted_means_latest_processed(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/paths", json_response({"info": {}}))
    async with make_client(recorder) as client:
        await client.path_search.get_paths(dst_ip="10.0.0.1")

    assert "snapshotId" not in recorder.query_for()
    assert recorder.query_for()["dstIp"] == ["10.0.0.1"]


async def test_required_query_parameter_is_required() -> None:
    """Path search needs a destination; omitting it fails before any request."""
    recorder = Recorder()
    async with make_client(recorder) as client:
        with pytest.raises(TypeError, match="dst_ip"):
            await client.path_search.get_paths()  # type: ignore[call-arg]


async def test_parameters_are_converted_to_api_names(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/paths", json_response({}))
    async with make_client(recorder) as client:
        await client.path_search.get_paths(
            dst_ip="10.0.0.1", src_ip="10.0.1.1", max_results=5, include_tags=True
        )

    query = recorder.query_for()
    assert query["srcIp"] == ["10.0.1.1"]
    assert query["maxResults"] == ["5"]
    assert query["includeTags"] == ["true"]


async def test_python_keyword_parameters_are_usable(recorder: Recorder) -> None:
    """Forward has a parameter named `from`, which needs a Python-safe name."""
    recorder.add("GET", "/api/networks/101/paths", json_response({}))
    async with make_client(recorder) as client:
        await client.path_search.get_paths(dst_ip="10.0.0.1", from_="sw1")

    assert recorder.query_for()["from"] == ["sw1"]


async def test_repeated_query_parameters(recorder: Recorder) -> None:
    recorder.add("GET", "/api/snapshots/9/checks", json_response([]))
    async with make_client(recorder) as client:
        await client.checks.get_checks(snapshot_id="9", type=["NQE", "Predefined"])

    assert recorder.query_for()["type"] == ["NQE", "Predefined"]


async def test_dispatched_operation_sends_its_selector(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/classic-devices", json_response({}))
    async with make_client(recorder) as client:
        await client.classic_devices.add_classic_devices(body=[{"name": "sw1"}])

    assert recorder.query_for()["action"] == ["addBatch"]
    assert recorder.body_for() == [{"name": "sw1"}]


async def test_constant_parameter_is_supplied(recorder: Recorder) -> None:
    """Forward fixes this parameter to one value, so the caller need not pass it."""
    recorder.add("POST", "/api/collector-tasks", json_response({}))
    async with make_client(recorder) as client:
        await client.collector_tasks.add_collector_task(network_id="101")

    assert recorder.query_for()["type"] == ["NETWORK_COLLECTION"]


async def test_path_segments_are_quoted(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/snapshots/9/aliases/site%2Fa",
        json_response(
            {
                "name": "site/a",
                "type": "HOSTS",
                "createdAt": "2026-01-01T00:00:00.000Z",
                "creatorId": "42",
            }
        ),
    )
    async with make_client(recorder) as client:
        await client.aliases.get_single_alias(snapshot_id="9", name="site/a")

    assert recorder.requests[0].url.raw_path.decode() == "/api/snapshots/9/aliases/site%2Fa"


async def test_deprecated_operation_warns(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/startcollection", json_response({}))
    async with make_client(recorder) as client:
        with pytest.warns(DeprecationWarning, match="collect"):
            await client.legacy_collection.collect()


async def test_streaming_operation_yields_bytes(recorder: Recorder) -> None:
    recorder.add("GET", "/api/cve-index", httpx.Response(200, content=b"gzipped"))
    async with make_client(recorder) as client:
        chunks = [c async for c in client.system_administration.get_cve_index()]

    assert b"".join(chunks) == b"gzipped"


async def test_licence_gated_group_is_documented_as_such(recorder: Recorder) -> None:
    """The gating hint reaches the caller through the raised error."""
    recorder.add("GET", "/api/networks/101/vulnerabilities", error_response(403, "not licensed"))
    async with make_client(recorder) as client:
        with pytest.raises(ForwardPermissionError) as caught:
            await client.vulnerability_analysis.get_vulnerabilities()

    assert caught.value.gating == ("license",)
