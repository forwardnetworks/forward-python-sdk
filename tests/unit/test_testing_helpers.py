"""The public test helpers drive a real client."""

from __future__ import annotations

import httpx

from forward_sdk import ForwardClient
from forward_sdk.testing import ForwardRecorder, snapshot_payload


def test_recorder_drives_latest_processed() -> None:
    recorder = ForwardRecorder()
    recorder.add(
        "GET",
        "/api/networks/101/snapshots",
        httpx.Response(
            200,
            json={
                "snapshots": [
                    snapshot_payload("old", created_at="2026-01-01T00:00:00Z"),
                    snapshot_payload("new", created_at="2026-01-02T00:00:00Z"),
                ]
            },
        ),
    )
    with ForwardClient(
        "https://forward.test",
        username="k",
        password="s",
        network_id="101",
        rate_limit_rpm=None,
        transport=recorder.transport,
    ) as client:
        latest = client.snapshots.latest_processed()

    assert latest is not None and latest.id == "new"
    assert recorder.count("GET", "/api/networks/101/snapshots") == 1


def test_execution_handle_and_execute_and_wait() -> None:
    base = "/api/networks/101/nqe-executions"
    recorder = ForwardRecorder()
    recorder.add("POST", base, httpx.Response(200, json={"executionKey": "e1"}))
    recorder.add(
        "GET", f"{base}/e1", httpx.Response(200, json={"status": "COMPLETED", "outcome": "OK"})
    )
    with ForwardClient(
        "https://forward.test",
        username="k",
        password="s",
        network_id="101",
        rate_limit_rpm=None,
        transport=recorder.transport,
    ) as client:
        done = client.nqe.execute_and_wait("q")
        handle = client.nqe.execution("e1")

    assert done.key == "e1"
    assert handle.key == "e1" and handle.network_id == "101"
