# Generated from tests/_async/test_security_analysis.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Security zones, the security matrix, resource pools, blast radius and
internet exposure: the unpublished remainder of Forward's security analysis
surface, added alongside the published Vulnerability Analysis group.

Most of these operations pass a caller-built body straight through -- the
generic builder's job, already covered elsewhere -- so what is worth checking
here is what is distinctive: the two blast-radius operations sharing one path
dispatched by a fixed query, the security-zones map response, and the
XLSX report streaming like any other binary export.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

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


def test_security_zones_is_a_device_to_zone_names_map(recorder: Recorder) -> None:
    recorder.add(
        "GET", "/api/networks/101/security-zones", json_response({"fw01": ["dmz", "inside"]})
    )
    with make_client(recorder) as client:
        zones = client.security_zones.get_security_zones()

    assert zones.root == {"fw01": ["dmz", "inside"]}


BLAST_BODY = {"source": {"type": "DeviceFilter", "value": "fw01"}, "dstSubnets": ["10.0.0.0/24"]}


def test_blast_radius_sends_no_dispatch_query(recorder: Recorder) -> None:
    recorder.add(
        "POST",
        "/api/networks/101/blast-radius",
        json_response({"details": [], "timedOut": False}),
    )
    with make_client(recorder) as client:
        client.blast_radius.get_blast_radius(body=BLAST_BODY)

    assert "type" not in recorder.query_for()


def test_host_centric_blast_radius_dispatches_on_the_same_path(recorder: Recorder) -> None:
    """Forward dispatches the host-centric variant on the same path by a fixed
    query string; it must reach the host-centric handler, not the plain one."""
    recorder.add(
        "POST",
        "/api/networks/101/blast-radius",
        json_response({"details": [], "numComputed": 3, "allComputed": True}),
    )
    with make_client(recorder) as client:
        result = client.blast_radius.get_host_centric_blast_radius(body=BLAST_BODY)

    assert recorder.query_for()["type"] == ["host-centric"]
    assert result.all_computed is True
    assert result.num_computed == 3


def test_resource_pool_is_sent_with_its_discriminator(recorder: Recorder) -> None:
    recorder.add("POST", "/api/networks/101/resource-pools", json_response({"timedOut": False}))
    pool = {
        "type": "ON_PREM",
        "name": "p1",
        "devices": ["fw01"],
        "vrfs": [],
        "subnets": ["10.0.0.0/24"],
    }
    with make_client(recorder) as client:
        client.resource_pools.analyze_resource_pool(body=pool)

    assert recorder.query_for()["action"] == ["analyze"]
    assert recorder.body_for() == pool


def test_host_centric_blast_radius_report_streams_the_workbook(recorder: Recorder) -> None:
    recorder.add(
        "POST",
        "/api/networks/101/blast-radius-report",
        httpx.Response(200, content=b"PK\x03\x04fake-xlsx"),
    )
    body = {"source": {"type": "DeviceFilter", "value": "fw01"}, "dstSubnets": ["10.0.0.0/24"]}
    with make_client(recorder) as client:
        chunks = [c for c in client.blast_radius.get_host_centric_blast_radius_report(body=body)]

    assert b"".join(chunks).startswith(b"PK\x03\x04")
    assert recorder.query_for()["type"] == ["host-centric"]


def test_security_matrix_filter_crud(recorder: Recorder) -> None:
    recorder.add(
        "POST",
        "/api/networks/101/securityMatrixFilters",
        json_response({"id": "1", "name": "f1", "resourcePools": [], "timeoutMins": 5}),
    )
    with make_client(recorder) as client:
        created = client.security_matrix_filters.add_security_matrix_filter(
            body={
                "name": "f1",
                "resourcePools": [{"type": "DEVICE_ZONE", "device": "fw01", "zone": "dmz"}],
            }
        )
        assert created.id == "1"

        recorder.add("DELETE", "/api/networks/101/securityMatrixFilters/1", json_response({}))
        client.security_matrix_filters.delete_security_matrix_filter(filter_id="1")

    assert recorder.requests[-1].url.path == "/api/networks/101/securityMatrixFilters/1"


def test_exposed_hosts_are_read_by_snapshot_not_network(recorder: Recorder) -> None:
    """This family hangs off the snapshot directly, unlike the rest of the group."""
    recorder.add(
        "GET",
        "/api/snapshots/9/internetNode/exposed-hosts",
        json_response({"hosts": [], "timedOut": False, "scannerUrls": [], "hostCounts": []}),
    )
    with make_client(recorder) as client:
        result = client.internet_exposure.get_internet_exposed_hosts(snapshot_id="9")

    assert result.hosts == []
    assert recorder.requests[0].url.path == "/api/snapshots/9/internetNode/exposed-hosts"
