"""T-API optical network containers: the one synthetic device family that is
created from uploaded documents rather than a JSON body."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

CONTAINERS = "/api/networks/101/tapi-network-containers"
CONTAINER = "/api/networks/101/tapi-network-containers/optical-core"


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


class TestReading:
    async def test_list_unwraps_the_envelope(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            CONTAINERS,
            json_response(
                {"containers": [{"name": "optical-core", "model": {"tapi-common:context": {}}}]}
            ),
        )
        async with make_client(recorder) as client:
            containers = await client.tapi_network_containers.list()

        assert [c.name for c in containers] == ["optical-core"]
        assert containers[0].model == {"tapi-common:context": {}}

    async def test_get_one(self, recorder: Recorder) -> None:
        recorder.add("GET", CONTAINER, json_response({"name": "optical-core", "model": {}}))
        async with make_client(recorder) as client:
            container = await client.tapi_network_containers.get("optical-core")

        assert container.name == "optical-core"


class TestWriting:
    async def test_put_sends_name_and_each_file_as_multipart(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        recorder.add("POST", CONTAINERS, httpx.Response(204))
        on_disk = tmp_path / "north.json"
        on_disk.write_bytes(b'{"north": true}')
        async with make_client(recorder) as client:
            await client.tapi_network_containers.put(
                "optical-core", [on_disk, ("south.json", b'{"south": true}')]
            )

        request = recorder.requests[-1]
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data")
        body = request.content
        assert b'name="name"\r\n\r\noptical-core' in body
        assert b'filename="north.json"' in body and b'{"north": true}' in body
        assert b'filename="south.json"' in body and b'{"south": true}' in body

    async def test_put_refuses_no_files(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="at least one file"):
                await client.tapi_network_containers.put("optical-core", [])
        assert recorder.requests == []

    async def test_delete_one_and_all(self, recorder: Recorder) -> None:
        recorder.add("DELETE", CONTAINER, httpx.Response(204))
        recorder.add("DELETE", CONTAINERS, httpx.Response(204))
        async with make_client(recorder) as client:
            await client.tapi_network_containers.delete("optical-core")
            await client.tapi_network_containers.delete_all()

        assert [r.method for r in recorder.requests] == ["DELETE", "DELETE"]

    async def test_backdate_names_the_snapshot(self, recorder: Recorder) -> None:
        recorder.add("POST", CONTAINERS, httpx.Response(204))
        async with make_client(recorder) as client:
            await client.tapi_network_containers.backdate("708")

        request = recorder.requests[-1]
        assert request.url.params["op"] == "backdate"
        assert request.url.params["snapshotId"] == "708"


class TestExecutionResultKey:
    async def test_result_key_comes_from_the_ui_status(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/nqe-executions",
            json_response({"executionKey": "exec-1", "status": "SUBMITTED"}),
        )
        recorder.add(
            "GET",
            "/api/networks/101/nqe-executions/exec-1",
            json_response({"status": "COMPLETED", "outcome": "OK", "resultKey": "R_" + "0" * 20}),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("foreach d in network.devices select {n: d.name}")
            key = await execution.result_key()

        assert key == "R_" + "0" * 20
        assert recorder.requests[-1].url.params["for"] == "ui"

    async def test_result_key_is_none_before_completion(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/nqe-executions",
            json_response({"executionKey": "exec-1", "status": "SUBMITTED"}),
        )
        recorder.add(
            "GET",
            "/api/networks/101/nqe-executions/exec-1",
            json_response({"status": "EXECUTING"}),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("foreach d in network.devices select {n: d.name}")
            assert await execution.result_key() is None
