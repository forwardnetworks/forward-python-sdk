# Generated from tests/_async/test_nqe.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Running queries: the sync endpoint, background executions, diffs, streaming."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
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


def page(rows: list[dict[str, Any]], total: int | None = None) -> httpx.Response:
    payload: dict[str, Any] = {"items": rows}
    if total is not None:
        payload["totalNumItems"] = total
    return json_response(payload)


class TestRun:
    def test_runs_inline_query(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([{"name": "sw1"}], total=1))
        with make_client(recorder) as client:
            result = client.nqe.run("foreach d in network.devices select {name: d.name}")

        assert result.total_num_items == 1
        assert result.items == [{"name": "sw1"}]
        body = recorder.body_for()
        assert body["query"].startswith("foreach")
        assert body["queryOptions"]["itemFormat"] == "JSON"
        assert recorder.query_for()["networkId"] == ["101"]

    def test_omits_snapshot_to_mean_latest_processed(self, recorder: Recorder) -> None:
        """Forward reads an absent snapshot as the network's latest processed one."""
        recorder.add("POST", "/api/nqe", page([]))
        with make_client(recorder) as client:
            client.nqe.run("q")

        assert "snapshotId" not in recorder.query_for()

    def test_explicit_snapshot_is_sent(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([]))
        with make_client(recorder) as client:
            client.nqe.run("q", snapshot_id="555")

        assert recorder.query_for()["snapshotId"] == ["555"]

    def test_latest_processed_sentinel_omits_the_parameter(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe", page([]))
        with make_client(recorder) as client:
            client.nqe.run("q", snapshot_id="latestProcessed")

        assert "snapshotId" not in recorder.query_for()

    def test_missing_network_is_a_configuration_error(self, recorder: Recorder) -> None:
        with make_client(recorder, network_id=None) as client:
            with pytest.raises(ForwardConfigurationError, match="no network id"):
                client.nqe.run("q")

    def test_query_errors_carry_source_positions(self, recorder: Recorder) -> None:
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
        with make_client(recorder) as client, pytest.raises(ForwardNqeQueryError) as caught:
            client.nqe.run("foreach d in x select {n: d.nmae}")

        assert caught.value.query_errors[0].message == "unknown field 'nmae'"


class TestExecute:
    def test_starts_execution_and_returns_handle(self, recorder: Recorder) -> None:
        recorder.add(
            "POST", EXECUTIONS, json_response({"executionKey": "exec-1", "status": "SUBMITTED"})
        )
        with make_client(recorder) as client:
            execution = client.nqe.execute(QueryRef.by_id("FQ_abc"))

        assert execution.key == "exec-1"
        assert execution.last_status == "SUBMITTED"
        assert recorder.body_for() == {"queryId": "FQ_abc"}

    def test_missing_execution_key_is_an_error(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"status": "SUBMITTED"}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match="no execution key"):
                client.nqe.execute("q")

    def test_wait_polls_until_complete(self, recorder: Recorder, no_sleep: list[float]) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            json_response({"status": "SUBMITTED"}),
            json_response({"status": "EXECUTING"}),
            json_response(COMPLETED),
        )
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            status = execution.wait()

        assert status["outcome"] == "OK"
        assert recorder.count("GET", STATUS) == 3
        assert execution.rows_produced == 2

    def test_wait_backs_off_between_polls(self, recorder: Recorder, no_sleep: list[float]) -> None:
        """Polling starts fast for short queries, then eases off."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add(
            "GET",
            STATUS,
            *[json_response({"status": "EXECUTING"})] * 5,
            json_response(COMPLETED),
        )
        with make_client(recorder) as client:
            (client.nqe.execute("q")).wait(poll_interval=5.0)

        assert no_sleep[0] < no_sleep[-1]
        assert max(no_sleep) <= 5.0

    def test_failed_execution_reports_the_outcome(
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
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError) as caught:
                (client.nqe.execute("q")).wait()

        assert caught.value.outcome == "TIMED_OUT"
        assert "time budget" in str(caught.value)

    def test_wait_timeout_leaves_execution_running(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response({"status": "EXECUTING"}))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            with pytest.raises(ForwardTimeoutError, match="still running"):
                execution.wait(timeout=0)


class TestResults:
    def test_rows_pages_until_total_reached(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add(
            "GET",
            RESULT,
            page([{"n": 1}, {"n": 2}], total=3),
            page([{"n": 3}], total=3),
        )
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            rows = [row for row in execution.rows(page_size=2)]

        assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]
        assert recorder.query_for()["offset"] == ["2"]

    def test_rows_unwraps_fields_envelope(self, recorder: Recorder) -> None:
        """Forward has returned rows both bare and wrapped in 'fields'."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"fields": {"n": 1}}], total=1))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            rows = [row for row in execution.rows(page_size=10)]

        assert rows == [{"n": 1}]

    def test_rows_stop_on_short_page_without_a_total(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}]))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            rows = [row for row in execution.rows(page_size=10)]

        assert rows == [{"n": 1}]
        assert recorder.count("GET", RESULT) == 1

    def test_non_advancing_paging_is_caught(self, recorder: Recorder) -> None:
        """A server ignoring the offset would otherwise loop forever."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}, {"n": 2}], total=1000))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            with pytest.raises(ForwardPaginationError, match="not advancing"):
                for _ in execution.rows(page_size=2, guards=PageGuards(repeat_limit=3)):
                    pass

    def test_row_ceiling_is_enforced(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add(
            "GET",
            RESULT,
            page([{"n": 1}, {"n": 2}], total=1000),
            page([{"n": 3}, {"n": 4}], total=1000),
        )
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            with pytest.raises(ForwardPaginationError, match="row limit"):
                for _ in execution.rows(page_size=2, guards=PageGuards(max_rows=3)):
                    pass

    def test_stream_reads_ndjson(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, ndjson_response([{"n": 1}, {"n": 2}]))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            rows = [row for row in execution.stream()]

        assert rows == [{"n": 1}, {"n": 2}]
        request = recorder.requests[-1]
        assert "ndjson" in request.headers["accept"]
        assert "limit" not in request.url.params

    def test_stream_falls_back_to_json_document(self, recorder: Recorder) -> None:
        """An older deployment may answer the stream request with plain JSON."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}], total=1))
        with make_client(recorder) as client:
            execution = client.nqe.execute("q")
            rows = [row for row in execution.stream()]

        assert rows == [{"n": 1}]

    def test_query_helper_runs_waits_and_collects(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, page([{"n": 1}], total=1))
        with make_client(recorder) as client:
            rows = client.nqe.query(QueryRef.by_id("FQ_abc"))

        assert rows == [{"n": 1}]
        assert client.counters.nqe_rows == 1

    def test_query_helper_can_stream(self, recorder: Recorder) -> None:
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        recorder.add("GET", STATUS, json_response(COMPLETED))
        recorder.add("GET", RESULT, ndjson_response([{"n": 1}]))
        with make_client(recorder) as client:
            rows = client.nqe.query("q", stream=True)

        assert rows == [{"n": 1}]


class TestDiff:
    def test_diff_pages_entries(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/nqe-diffs/100/101",
            json_response({"rows": [{"type": "ADDED"}], "totalNumRows": 1}),
        )
        with make_client(recorder) as client:
            entries = client.nqe.diff("100", "101", QueryRef.by_id("FQ_abc"))

        assert entries == [{"type": "ADDED"}]
        assert recorder.body_for()["queryId"] == "FQ_abc"

    def test_diff_rejects_inline_source(self, recorder: Recorder) -> None:
        """Forward cannot diff query source it has never seen."""
        with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="committed query"):
                client.nqe.diff("100", "101", "foreach d in x select {}")


class TestLibrary:
    def test_lists_queries(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/queries",
            json_response([{"queryId": "FQ_1", "path": "/L2/Mtu", "repository": "ORG"}]),
        )
        with make_client(recorder) as client:
            queries = client.nqe.queries(directory="/L2")

        assert queries[0].query_id == "FQ_1"
        assert recorder.query_for()["dir"] == ["/L2"]

    def test_path_is_resolved_before_running(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/repos/org/commits/head/queries",
            json_response({"queries": [{"queryId": "FQ_9", "path": "/NetBox/Devices"}]}),
        )
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        with make_client(recorder) as client:
            client.nqe.execute(QueryRef.by_path("/NetBox/Devices"))

        assert recorder.body_for() == {"queryId": "FQ_9"}

    def test_unknown_path_is_reported_clearly(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", "/api/nqe/repos/org/commits/head/queries", json_response({"queries": []})
        )
        with make_client(recorder) as client, pytest.raises(Exception, match="no query at"):
            client.nqe.execute(QueryRef.by_path("/Missing"))

    def test_query_index_is_cached_across_resolutions(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/nqe/repos/org/commits/head/queries",
            json_response(
                {"queries": [{"queryId": "FQ_9", "path": "/A"}, {"queryId": "FQ_8", "path": "/B"}]}
            ),
        )
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        with make_client(recorder) as client:
            client.nqe.execute(QueryRef.by_path("/A"))
            client.nqe.execute(QueryRef.by_path("/B"))

        assert recorder.count("GET", "/api/nqe/repos/org/commits/head/queries") == 1
        assert client.counters.cache_hits == 1

    def test_abbreviated_commit_id_is_dropped(self, recorder: Recorder) -> None:
        """Forward rejects an abbreviated hash, so sending it would fail the run."""
        recorder.add("POST", EXECUTIONS, json_response({"executionKey": "exec-1"}))
        with make_client(recorder) as client:
            client.nqe.execute(QueryRef.by_id("FQ_abc", commit_id="84f84b0"))

        assert "commitId" not in recorder.body_for()
