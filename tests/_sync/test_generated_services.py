# Generated from tests/_async/test_generated_services.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

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

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardPermissionError
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder, **overrides: Any) -> ForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "network_id": "101",
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return ForwardClient("https://forward.test", **settings)


def test_response_is_parsed_into_its_model(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/locations",
        json_response([{"id": "loc-1", "name": "New York", "lat": 40.7, "lng": -74.0}]),
    )
    with make_client(recorder) as client:
        locations = client.network_locations.get_locations()

    assert locations[0].name == "New York"
    assert locations[0].id == "loc-1"


def test_network_defaults_to_the_client_setting(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/locations", json_response([]))
    with make_client(recorder) as client:
        client.network_locations.get_locations()

    assert recorder.paths == ["/api/networks/101/locations"]


def test_explicit_network_wins(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/999/locations", json_response([]))
    with make_client(recorder) as client:
        client.network_locations.get_locations(network_id="999")

    assert recorder.paths == ["/api/networks/999/locations"]


def test_snapshot_omitted_means_latest_processed(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/paths", json_response({"info": {}}))
    with make_client(recorder) as client:
        client.path_search.get_paths(dst_ip="10.0.0.1")

    assert "snapshotId" not in recorder.query_for()
    assert recorder.query_for()["dstIp"] == ["10.0.0.1"]


def test_required_query_parameter_is_required() -> None:
    """Path search needs a destination; omitting it fails before any request."""
    recorder = Recorder()
    with make_client(recorder) as client:
        with pytest.raises(TypeError, match="dst_ip"):
            client.path_search.get_paths()  # type: ignore[call-arg]


def test_parameters_are_converted_to_api_names(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/paths", json_response({}))
    with make_client(recorder) as client:
        client.path_search.get_paths(
            dst_ip="10.0.0.1", src_ip="10.0.1.1", max_results=5, include_tags=True
        )

    query = recorder.query_for()
    assert query["srcIp"] == ["10.0.1.1"]
    assert query["maxResults"] == ["5"]
    assert query["includeTags"] == ["true"]


def test_python_keyword_parameters_are_usable(recorder: Recorder) -> None:
    """Forward has a parameter named `from`, which needs a Python-safe name."""
    recorder.add("GET", "/api/networks/101/paths", json_response({}))
    with make_client(recorder) as client:
        client.path_search.get_paths(dst_ip="10.0.0.1", from_="sw1")

    assert recorder.query_for()["from"] == ["sw1"]


def test_repeated_query_parameters(recorder: Recorder) -> None:
    recorder.add("GET", "/api/snapshots/9/checks", json_response([]))
    with make_client(recorder) as client:
        client.checks.get_checks(snapshot_id="9", type=["NQE", "Predefined"])

    assert recorder.query_for()["type"] == ["NQE", "Predefined"]


def test_dispatched_operation_sends_its_selector(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/classic-devices", json_response({}))
    with make_client(recorder) as client:
        client.classic_devices.add_classic_devices(body=[{"name": "sw1"}])

    assert recorder.query_for()["action"] == ["addBatch"]
    assert recorder.body_for() == [{"name": "sw1"}]


def test_constant_parameter_is_supplied(recorder: Recorder) -> None:
    """Forward fixes this parameter to one value, so the caller need not pass it."""
    recorder.add("POST", "/api/collector-tasks", json_response({}))
    with make_client(recorder) as client:
        client.collector_tasks.add_collector_task(network_id="101")

    assert recorder.query_for()["type"] == ["NETWORK_COLLECTION"]


def test_path_segments_are_quoted(recorder: Recorder) -> None:
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
    with make_client(recorder) as client:
        client.aliases.get_single_alias(snapshot_id="9", name="site/a")

    assert recorder.requests[0].url.raw_path.decode() == "/api/snapshots/9/aliases/site%2Fa"


def test_deprecated_operation_warns(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/startcollection", json_response({}))
    with make_client(recorder) as client:
        with pytest.warns(DeprecationWarning, match="collect"):
            client.legacy_collection.collect()


def test_streaming_operation_yields_bytes(recorder: Recorder) -> None:
    recorder.add("GET", "/api/cve-index", httpx.Response(200, content=b"gzipped"))
    with make_client(recorder) as client:
        chunks = [c for c in client.system_administration.get_cve_index()]

    assert b"".join(chunks) == b"gzipped"


def test_licence_gated_group_is_documented_as_such(recorder: Recorder) -> None:
    """The gating hint reaches the caller through the raised error."""
    recorder.add("GET", "/api/networks/101/vulnerabilities", error_response(403, "not licensed"))
    with make_client(recorder) as client:
        with pytest.raises(ForwardPermissionError) as caught:
            client.vulnerability_analysis.get_vulnerabilities()

    assert caught.value.gating == ("license",)


def test_org_properties_are_listed_with_a_filter(recorder: Recorder) -> None:
    recorder.add("GET", "/api/config", json_response({"firewall_predict": True}))
    with make_client(recorder) as client:
        listing = client.configuration.get_org_config_values(filter="CONFIGURED")

    assert recorder.query_for()["filter"] == ["CONFIGURED"]
    assert listing.model_dump(by_alias=True) == {"firewall_predict": True}


def test_global_properties_are_listed_from_their_own_route(recorder: Recorder) -> None:
    """The global route reports defaults only, never an org's override."""
    recorder.add("GET", "/api/global-config", json_response({"firewall_predict": False}))
    with make_client(recorder) as client:
        client.configuration.get_global_config_values(filter="CONFIGURED")

    assert recorder.paths == ["/api/global-config"]


def test_no_filter_is_left_to_forward_to_default(recorder: Recorder) -> None:
    # The default differs by route (CONFIGURABLE here, OFF for a support account),
    # so the SDK must not impose one.
    recorder.add("GET", "/api/config", json_response({}))
    with make_client(recorder) as client:
        client.configuration.get_org_config_values()

    assert "filter" not in recorder.query_for()


def test_a_support_account_lists_another_orgs_properties(recorder: Recorder) -> None:
    recorder.add("GET", "/api/orgs/42/config", json_response({"firewall_predict": True}))
    with make_client(recorder) as client:
        client.configuration.get_org_scoped_config_values(org_id="42")

    assert recorder.paths == ["/api/orgs/42/config"]


def test_a_devices_own_collector_is_read_and_sent_as_given(recorder: Recorder) -> None:
    """Forward binds collectorId but leaves it out of its description; it is
    declared here so it is typed on a read and survives a write unchanged."""
    recorder.add(
        "GET",
        "/api/networks/101/classic-devices",
        json_response(
            {
                "devices": [
                    {
                        "name": "r1",
                        "host": "10.0.0.1",
                        "type": "cisco_ios_ssh",
                        "collectorId": "C3",
                    },
                    {"name": "r2", "host": "10.0.0.2", "type": "cisco_ios_ssh"},
                ]
            }
        ),
    )
    with make_client(recorder) as client:
        listing = client.classic_devices.get_classic_devices()

    first, second = listing.devices or []
    assert first.collector_id == "C3"
    assert second.collector_id is None


def test_a_collector_is_only_sent_when_the_caller_sets_one(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/classic-devices", json_response({}, status=201))
    with make_client(recorder) as client:
        client.classic_devices.put_classic_devices(
            body=[
                {"name": "r1", "host": "10.0.0.1", "type": "cisco_ios_ssh", "collectorId": "C3"},
                {"name": "r2", "host": "10.0.0.2", "type": "cisco_ios_ssh"},
            ]
        )

    sent = recorder.body_for()
    assert sent[0]["collectorId"] == "C3"
    assert "collectorId" not in sent[1], "an unset collector must not be sent as null"
