# Generated from tests/_async/test_connectivity_tests.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Connectivity tests: probing a classic device or network endpoint on demand
rather than waiting for the next scheduled collection.

Most of these operations are ordinary generated shapes; what is worth testing
here is what is distinctive: the classic-device/endpoint dispatch by a fixed
query on shared paths, the single-source read reusing the published
SourceConnectivityResult directly, and the phase-by-phase breakdown, which is
a discriminated union generated as a RootModel -- ``.root`` gets the concrete
phase.
"""

from __future__ import annotations

from typing import Any

import pytest

from forward_sdk import models
from forward_sdk._sync.client import ForwardClient
from tests.conftest import Recorder, json_response

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


def test_bulk_start_sends_a_device_set(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/connectivityTests/bulkStart", json_response(None))
    with make_client(recorder) as client:
        client.connectivity_tests.bulk_start_connectivity_tests(body={"devices": ["fw01"]})

    assert "type" not in recorder.query_for()
    assert recorder.body_for() == {"devices": ["fw01"]}


def test_bulk_start_for_endpoints_dispatches_on_the_same_path(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/connectivityTests/bulkStart", json_response(None))
    with make_client(recorder) as client:
        client.connectivity_tests.bulk_start_network_endpoint_connectivity_tests(
            body={"devices": ["ep01"]}
        )

    assert recorder.query_for()["type"] == ["endpoint"]


def test_start_one_for_an_endpoint_dispatches_on_the_same_path(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/connectivityTests/ep01/start", json_response(None))
    with make_client(recorder) as client:
        client.connectivity_tests.start_network_endpoint_connectivity_test(device_name="ep01")

    assert recorder.query_for()["type"] == ["endpoint"]


def test_single_result_reuses_the_published_response_shape(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/connectivityTests/fw01",
        json_response(
            {
                "startTime": "2026-01-01T00:00:00.000Z",
                "endTime": "2026-01-01T00:00:02.000Z",
                "savedAt": "2026-01-01T00:00:02.000Z",
                "hostIps": ["10.0.0.1"],
            }
        ),
    )
    with make_client(recorder) as client:
        result = client.connectivity_tests.get_connectivity_test_result(device_name="fw01")

    assert result.host_ips == ["10.0.0.1"]


def test_list_results_carries_the_device_name(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/connectivityTests",
        json_response(
            [
                {
                    "deviceName": "fw01",
                    "startTime": "2026-01-01T00:00:00.000Z",
                    "endTime": "2026-01-01T00:00:02.000Z",
                    "savedAt": "2026-01-01T00:00:02.000Z",
                }
            ]
        ),
    )
    with make_client(recorder) as client:
        results = client.connectivity_tests.get_connectivity_test_results()

    assert results[0].device_name == "fw01"


def test_phase_results_are_a_discriminated_union(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/connectivityTests/fw01",
        json_response(
            [
                {"phase": "CONNECTION", "availableProtocols": ["SSH"]},
                {"phase": "AUTHENTICATION", "discoveredCliCredentialId": "L-1"},
            ]
        ),
    )
    with make_client(recorder) as client:
        phases = client.connectivity_tests.get_connectivity_test_phase_results(device_name="fw01")

    assert recorder.query_for()["view"] == ["phaseResults"]
    connection, authentication = (p.root for p in phases)
    assert isinstance(connection, models.ConnectionPhaseResult)
    assert connection.phase == "CONNECTION"
    assert connection.available_protocols == ["SSH"]
    assert isinstance(authentication, models.AuthenticationPhaseResult)
    assert authentication.discovered_cli_credential_id == "L-1"
