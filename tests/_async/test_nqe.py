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

        assert str(status.outcome) == "OK"
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

    async def test_wait_skips_polling_when_already_finished(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """A short query can finish in the response that started it."""
        recorder.add(
            "POST",
            EXECUTIONS,
            json_response({"executionKey": "exec-1", **COMPLETED}),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            status = await execution.wait()

        assert str(status.outcome) == "OK"
        assert recorder.count("GET", STATUS) == 0
        assert no_sleep == []

    async def test_wait_honours_retry_after_while_polling(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Forward's pacing request wins over the client's own schedule."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "EXECUTING"}, headers={"Retry-After": "12"}),
            json_response(COMPLETED),
        )
        async with make_client(recorder) as client:
            await (await client.nqe.execute("q")).wait()

        assert no_sleep == [12.0]

    async def test_wait_stops_at_the_budget_forward_allows(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Past Forward's own budget the execution can only end in a timeout."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "EXECUTING", "timeoutMinutes": 0}),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            with pytest.raises(ForwardTimeoutError, match="budget Forward"):
                # A local timeout far beyond Forward's own budget.
                await execution.wait(timeout=100_000)

    async def test_server_budget_is_read_from_the_status(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response({"status": "EXECUTING", "timeoutMinutes": 30}))
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            assert execution.server_deadline is None  # not yet known
            await execution.status()
            assert execution.server_deadline is not None

    async def test_execution_reports_forward_progress(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "EXECUTING", "millisExecuting": 4200, "rowsProduced": 17}),
        )
        async with make_client(recorder) as client:
            execution = await client.nqe.execute("q")
            await execution.status()

        assert execution.millis_executing == 4200
        assert execution.rows_produced == 17
        assert execution.is_finished is False

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


class TestTelemetry:
    async def test_client_retains_a_record_of_each_execution(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """A sync collects telemetry at the end, long after the handles are gone."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "EXECUTING"}),
            json_response({**COMPLETED, "millisExecuting": 250}),
        )
        recorder.add("GET", RESULT, page([{"n": 1}], total=1))

        async with make_client(recorder) as client:
            await client.nqe.query("foreach d in network.devices select {n: d.name}")
            reports = client.nqe.execution_reports()

        assert len(reports) == 1
        report = reports[0]
        assert report.execution_key == "exec-1"
        assert report.network_id == "101"
        assert report.terminal_reason == "OK"
        assert report.rows_produced == 2
        assert report.millis_executing == 250
        assert report.poll_count == 2
        assert report.poll_sleep_seconds > 0
        assert report.timeout_minutes is None
        assert "inline query" in (report.query or "")
        assert set(report.as_dict()) >= {"execution_key", "terminal_reason"}

    async def test_report_records_the_budget_and_the_pacing_forward_asked_for(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Both explain a slow run, and both go into a support bundle."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response(
                {"status": "EXECUTING", "timeoutMinutes": 30},
                headers={"Retry-After": "8"},
            ),
            json_response({**COMPLETED, "timeoutMinutes": 30}),
        )
        async with make_client(recorder) as client:
            await (await client.nqe.execute("q")).wait()
            report = client.nqe.execution_reports()[0]

        assert report.timeout_minutes == 30
        assert report.retry_after_seconds == 8.0

    async def test_a_failed_execution_is_recorded_with_its_reason(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Telemetry is most wanted on the failure path, so it must be there."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response({"status": "COMPLETED", "outcome": "TIMED_OUT"}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError):
                await client.nqe.query("q")
            reports = client.nqe.execution_reports()

        assert [r.terminal_reason for r in reports] == ["TIMED_OUT"]

    async def test_client_timeout_is_recorded(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response({"status": "EXECUTING"}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError):
                await (await client.nqe.execute("q")).wait(timeout=0)
            reports = client.nqe.execution_reports()

        assert [r.terminal_reason for r in reports] == ["CLIENT_TIMEOUT"]


class TestDiff:
    async def test_diff_pages_entries(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/nqe-diffs/100/101",
            json_response({"rows": [{"type": "ADDED"}], "totalNumRows": 1}),
        )
        async with make_client(recorder) as client:
            entries = await client.nqe.diff(QueryRef.by_id("FQ_abc"), before="100", after="101")

        assert len(entries) == 1
        assert str(entries[0].type) == "ADDED"
        assert recorder.body_for()["queryId"] == "FQ_abc"

    async def test_diff_calls_and_pages_are_counted_apart_from_queries(
        self, recorder: Recorder
    ) -> None:
        """A release gate reads these separately; they are different workloads."""
        recorder.add(
            "POST",
            "/api/nqe-diffs/100/101",
            json_response({"rows": [{"type": "ADDED"}], "totalNumRows": 1}),
        )
        async with make_client(recorder) as client:
            await client.nqe.diff(QueryRef.by_id("FQ_abc"), before="100", after="101")
            counters = client.counters

        assert counters.nqe_diff_calls == 1
        assert counters.nqe_diff_pages == 1
        assert counters.nqe_pages == 0

    async def test_execute_accepts_parameters_and_sort_keys(self, recorder: Recorder) -> None:
        """Matching run(), for callers not building a QueryRef up front."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(
                QueryRef.by_id("FQ_abc"), parameters={"site": "nyc"}, sort_keys=["name"]
            )

        body = recorder.body_for()
        assert body["parameters"] == {"site": "nyc"}
        assert body["sortKeys"] == [{"columnName": "name", "order": "ASC"}]

    async def test_diff_rejects_inline_source(self, recorder: Recorder) -> None:
        """Forward cannot diff query source it has never seen."""
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="committed query"):
                await client.nqe.diff("foreach d in x select {}", before="100", after="101")


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

    async def test_resolving_a_path_keeps_the_commit_pin(self, recorder: Recorder) -> None:
        """Forward nests the commit as lastCommit.id.

        Reading only the flat key loses the pin on every path resolution, so the
        query runs against whatever is at head. Nothing fails; the run just
        quietly answers a different question.
        """
        commit = "84f84b0c0a0a1805ddff0ca5451c2c55c58605e5"
        recorder.add(
            "GET",
            "/api/nqe/repos/org/commits/head/queries",
            json_response(
                {
                    "queries": [
                        {
                            "queryId": "FQ_9",
                            "path": "/NetBox/Devices",
                            "lastCommit": {"id": commit},
                        }
                    ]
                }
            ),
        )
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(QueryRef.by_path("/NetBox/Devices"))

        assert recorder.body_for() == {"queryId": "FQ_9", "commitId": commit}

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

    async def test_abbreviated_commit_id_is_refused(self, recorder: Recorder) -> None:
        """Pinning to a short hash must fail loudly, not quietly run at head.

        Silently dropping the pin would answer a different question and report
        success, which is what pinning exists to prevent.
        """
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="abbreviated"):
                await client.nqe.execute(QueryRef.by_id("FQ_abc", commit_id="84f84b0"))

        assert recorder.requests == []

    async def test_head_commit_id_is_dropped_silently(self, recorder: Recorder) -> None:
        """`head` is not a hash; omitting the field means the same thing."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        async with make_client(recorder) as client:
            await client.nqe.execute(QueryRef.by_id("FQ_abc", commit_id="head"))

        assert "commitId" not in recorder.body_for()
