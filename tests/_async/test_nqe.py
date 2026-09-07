"""Running queries: the sync endpoint, background executions, diffs, streaming."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardExecutionError,
    ForwardNqeQueryError,
    ForwardPaginationError,
    ForwardTimeoutError,
)
from forward_sdk.nqe import PageGuards, QueryRef
from tests.conftest import Recorder, json_response, ndjson_response

pytestmark = pytest.mark.anyio

EXECUTIONS = "/api/networks/101/nqe-executions"
STATUS = "/api/networks/101/nqe-executions/exec-1"
RESULT = "/api/networks/101/nqe-executions/exec-1/result"

COMPLETED = {"status": "COMPLETED", "outcome": "OK", "rowsProduced": 2}


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


def page(rows: list[dict[str, Any]], total: int | None = None) -> httpx.Response:
    payload: dict[str, Any] = {"items": rows}
    if total is not None:
        payload["totalNumItems"] = total
    return json_response(payload)


class TestRun:
    async def test_runs_inline_query(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([{"name": "sw1"}], total=1))
        async with make_client(recorder) as client:
            result = await client.nqe.run("foreach d in network.devices select {name: d.name}")

        assert result.total_num_items == 1
        assert result.items == [{"name": "sw1"}]
        body = recorder.body_for()
        assert body["query"].startswith("foreach")
        assert body["queryOptions"]["itemFormat"] == "JSON"
        assert recorder.query_for()["networkId"] == ["101"]

    async def test_omits_snapshot_to_mean_latest_processed(self, recorder: Recorder) -> None:
        """Forward reads an absent snapshot as the network's latest processed one."""
        recorder.add("POST", "/api/nqe", page([]))
        async with make_client(recorder) as client:
            await client.nqe.run("q")

        assert "snapshotId" not in recorder.query_for()

    async def test_explicit_snapshot_is_sent(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([]))
        async with make_client(recorder) as client:
            await client.nqe.run("q", snapshot_id="555")

        assert recorder.query_for()["snapshotId"] == ["555"]

    async def test_latest_processed_sentinel_omits_the_parameter(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([]))
        async with make_client(recorder) as client:
            await client.nqe.run("q", snapshot_id="latestProcessed")

        assert "snapshotId" not in recorder.query_for()

    async def test_missing_network_is_a_configuration_error(self, recorder: Recorder) -> None:
        async with make_client(recorder, network_id=None) as client:
            with pytest.raises(ForwardConfigurationError, match="no network id"):
                await client.nqe.run("q")

    async def test_query_errors_carry_source_positions(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/nqe",
            httpx.Response(
                400,
                json={
                    "httpMethod": "POST",
                    "apiUrl": "/api/nqe",
                    "message": "compilation failed",
                    "errors": [{"message": "unknown field 'nmae'"}],
                },
            ),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNqeQueryError) as caught:
                await client.nqe.run("foreach d in x select {n: d.nmae}")

        assert caught.value.query_errors[0].message == "unknown field 'nmae'"


class TestExecute:
    async def test_starts_execution_and_returns_handle(self, recorder: Recorder) -> None:
        recorder.add(
            "POST", EXECUTIONS, json_response({"executionKey": "exec-1", "status": "SUBMITTED"})
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute(QueryRef.by_id("FQ_abc"))

        assert execution.key == "exec-1"
        assert execution.last_status == "SUBMITTED"
        assert recorder.body_for() == {"queryId": "FQ_abc"}

    async def test_missing_execution_key_is_an_error(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"status": "SUBMITTED"}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match="no execution key"):
                await client.nqe.execute("q")

    async def test_wait_polls_until_complete(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "SUBMITTED"}),
            json_response({"status": "EXECUTING"}),
            json_response(COMPLETED),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            status = await execution.wait()

        assert status["outcome"] == "OK"
        assert recorder.count("GET", STATUS) == 3
        assert execution.rows_produced == 2

    async def test_wait_backs_off_between_polls(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Polling starts fast for short queries, then eases off."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            *[json_response({"status": "EXECUTING"})] * 5,
            json_response(COMPLETED),
        )
        async with make_client(recorder) as client:
            await (await client.nqe.execute("q")).wait(poll_interval=5.0)

        assert no_sleep[0] < no_sleep[-1]
        assert max(no_sleep) <= 5.0

    async def test_failed_execution_reports_the_outcome(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response(
                {
                    "status": "COMPLETED",
                    "outcome": "TIMED_OUT",
                    "error": {"message": "query exceeded its time budget"},
                }
            ),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError) as caught:
                await (await client.nqe.execute("q")).wait()

        assert caught.value.outcome == "TIMED_OUT"
        assert "time budget" in str(caught.value)

    async def test_wait_timeout_leaves_execution_running(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response({"status": "EXECUTING"}))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            with pytest.raises(ForwardTimeoutError, match="still running"):
                await execution.wait(timeout=0)


class TestResults:
    async def test_rows_pages_until_total_reached(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add(
            "GET",
            RESULT,
            page([{"n": 1}, {"n": 2}], total=3),
            page([{"n": 3}], total=3),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            rows = [row async for row in execution.rows(page_size=2)]

        assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]
        assert recorder.query_for()["offset"] == ["2"]

    async def test_rows_unwraps_fields_envelope(self, recorder: Recorder) -> None:
        """Forward has returned rows both bare and wrapped in 'fields'."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"fields": {"n": 1}}], total=1))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            rows = [row async for row in execution.rows(page_size=10)]

        assert rows == [{"n": 1}]

    async def test_rows_stop_on_short_page_without_a_total(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}]))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            rows = [row async for row in execution.rows(page_size=10)]

        assert rows == [{"n": 1}]
        assert recorder.count("GET", RESULT) == 1

    async def test_non_advancing_paging_is_caught(self, recorder: Recorder) -> None:
        """A server ignoring the offset would otherwise loop forever."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}, {"n": 2}], total=1000))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            with pytest.raises(ForwardPaginationError, match="not advancing"):
                async for _ in execution.rows(page_size=2, guards=PageGuards(repeat_limit=3)):
                    pass

    async def test_row_ceiling_is_enforced(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add(
            "GET",
            RESULT,
            page([{"n": 1}, {"n": 2}], total=1000),
            page([{"n": 3}, {"n": 4}], total=1000),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            with pytest.raises(ForwardPaginationError, match="row limit"):
                async for _ in execution.rows(page_size=2, guards=PageGuards(max_rows=3)):
                    pass

    async def test_stream_reads_ndjson(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, ndjson_response([{"n": 1}, {"n": 2}]))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            rows = [row async for row in execution.stream()]

        assert rows == [{"n": 1}, {"n": 2}]
        request = recorder.requests[-1]
        assert "ndjson" in request.headers["accept"]
        assert "limit" not in request.url.params

    async def test_stream_falls_back_to_json_document(self, recorder: Recorder) -> None:
        """An older deployment may answer the stream request with plain JSON."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}], total=1))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            rows = [row async for row in execution.stream()]

        assert rows == [{"n": 1}]

    async def test_query_helper_runs_waits_and_collects(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}], total=1))
        async with make_client(recorder) as client:
            rows = await client.nqe.query(QueryRef.by_id("FQ_abc"))

        assert rows == [{"n": 1}]
        assert client.counters.nqe_rows == 1

    async def test_query_helper_can_stream(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, ndjson_response([{"n": 1}]))
        async with make_client(recorder) as client:
            rows = await client.nqe.query("q", stream=True)

        assert rows == [{"n": 1}]


class TestDiff:
    async def test_diff_pages_entries(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/nqe-diffs/100/101",
            json_response({"rows": [{"type": "ADDED"}], "totalNumRows": 1}),
        )
        async with make_client(recorder) as client:
            entries = await client.nqe.diff("100", "101", QueryRef.by_id("FQ_abc"))

        assert entries == [{"type": "ADDED"}]
        assert recorder.body_for()["queryId"] == "FQ_abc"

    async def test_diff_rejects_inline_source(self, recorder: Recorder) -> None:
        """Forward cannot diff query source it has never seen."""
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="committed query"):
                await client.nqe.diff("100", "101", "foreach d in x select {}")


class TestLibrary:
    async def test_lists_queries(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/queries",
            json_response([{"queryId": "FQ_1", "path": "/L2/Mtu", "repository": "ORG"}]),
        )
        async with make_client(recorder) as client:
            queries = await client.nqe.queries(directory="/L2")

        assert queries[0].query_id == "FQ_1"
        assert recorder.query_for()["dir"] == ["/L2"]

    async def test_path_is_resolved_before_running(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/repos/org/commits/head/queries",
            json_response({"queries": [{"queryId": "FQ_9", "path": "/NetBox/Devices"}]}),
        )
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(QueryRef.by_path("/NetBox/Devices"))

        assert recorder.body_for() == {"queryId": "FQ_9"}

    async def test_unknown_path_is_reported_clearly(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", "/api/nqe/repos/org/commits/head/queries", json_response({"queries": []})
        )
        async with make_client(recorder) as client:
            with pytest.raises(Exception, match="no query at"):
                await client.nqe.execute(QueryRef.by_path("/Missing"))

    async def test_query_index_is_cached_across_resolutions(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/repos/org/commits/head/queries",
            json_response(
                {"queries": [{"queryId": "FQ_9", "path": "/A"}, {"queryId": "FQ_8", "path": "/B"}]}
            ),
        )
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(QueryRef.by_path("/A"))
            await client.nqe.execute(QueryRef.by_path("/B"))

        assert recorder.count("GET", "/api/nqe/repos/org/commits/head/queries") == 1
        assert client.counters.cache_hits == 1

    async def test_abbreviated_commit_id_is_dropped(self, recorder: Recorder) -> None:
        """Forward rejects an abbreviated hash, so sending it would fail the run."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(QueryRef.by_id("FQ_abc", commit_id="84f84b0"))

        assert "commitId" not in recorder.body_for()
