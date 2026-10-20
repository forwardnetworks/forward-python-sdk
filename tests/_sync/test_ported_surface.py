# Generated from tests/_async/test_ported_surface.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Calls brought over from the Go SDK, checked on the wire.

Each test states what Forward requires of the request and reads it back from the
recorded request, so a wrong path, query or body shows up here and not on a
customer's network. The one with real consequences is the retention preview:
the same route deletes snapshots when ``dryRun=true`` is missing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardNotFoundError
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

CS = "/api/networks/101/change-sets/CHG-1"


def make_client(recorder: Recorder) -> ForwardClient:
    return ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


def text_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=text.encode(), headers={"content-type": "text/plain"})


class TestSnapshots:
    def test_the_retention_preview_can_never_run_the_cleanup(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/snapshotRetentionPolicy/trigger",
            json_response({"count": 0, "deletedSnapshots": []}),
        )
        with make_client(recorder) as client:
            client.snapshots.preview_retention()
        request = recorder.requests[0]
        assert request.url.params.get_list("dryRun") == ["true"]

    def test_favorite_and_unfavorite_pick_their_action(self, recorder: Recorder) -> None:
        recorder.add("PATCH", "/api/snapshots/7", json_response({}))
        with make_client(recorder) as client:
            client.snapshots.favorite("7")
            client.snapshots.unfavorite("7")
        assert [r.url.params.get("action") for r in recorder.requests] == ["favorite", "unfavorite"]

    def test_a_note_is_sent_as_a_body_not_a_query(self, recorder: Recorder) -> None:
        recorder.add("PATCH", "/api/snapshots/7", json_response({"id": "7", "note": "x"}))
        with make_client(recorder) as client:
            meta = client.snapshots.set_note("7", "before the change")
        assert recorder.body_for() == {"note": "before the change"}
        assert "action" not in recorder.query_for()
        assert meta["id"] == "7"

    def test_the_policy_is_replaced_with_every_field(self, recorder: Recorder) -> None:
        recorder.add("PUT", "/api/networks/101/snapshotRetentionPolicy", httpx.Response(204))
        policy = {
            "enabled": True,
            "lastWeek": 7,
            "lastMonth": 4,
            "lastQuarter": 3,
            "lastYear": 4,
            "older": 1,
        }
        with make_client(recorder) as client:
            client.snapshots.set_retention_policy(policy)
        assert recorder.body_for() == policy

    def test_logs_are_streamed_with_their_filters(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/snapshots/7/logs", text_response("line one\nline two\n"))
        with make_client(recorder) as client:
            chunks = [c for c in client.snapshots.logs("7", device_name="r1", level="ERROR")]
        assert b"".join(chunks) == b"line one\nline two\n"
        assert recorder.query_for() == {"deviceName": ["r1"], "level": ["ERROR"]}

    def test_collection_exceptions_name_the_snapshot_in_the_query(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/collector/exceptions", json_response({"exceptions": []}))
        with make_client(recorder) as client:
            client.snapshots.collection_exceptions("7", limit=5)
        assert recorder.query_for() == {"snapshotId": ["7"], "limit": ["5"]}


class TestChangeSetCommitsAndConfigs:
    def test_a_commit_note_travels_in_the_query(self, recorder: Recorder) -> None:
        recorder.add("PATCH", f"{CS}/commits/C9", json_response({"commitId": "C9", "note": "ok"}))
        with make_client(recorder) as client:
            commit = client.change_sets.handle("CHG-1").edit_commit_note("C9", "ok")
        assert recorder.query_for() == {"note": ["ok"]}
        assert commit.commit_id == "C9"

    def test_restoring_as_new_names_the_copy_in_the_query(self, recorder: Recorder) -> None:
        recorder.add("POST", f"{CS}/commits/C9", json_response({"id": "CHG-2"}))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").restore_commit_as_new("C9", name="redo")
        assert recorder.query_for()["action"] == ["restoreAsNew"]
        assert recorder.query_for()["targetName"] == ["redo"]

    def test_a_copy_into_another_network_sends_that_network_once(self, recorder: Recorder) -> None:
        recorder.add("POST", CS, json_response({"id": "CHG-3"}))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").duplicate(
                target_network_id="202", base_snapshot_id="55"
            )
        assert recorder.query_for() == {"action": ["duplicate"], "targetNetworkId": ["202"]}
        assert recorder.body_for() == {"targetBaseSnapshotId": "55"}

    def test_a_copy_into_another_network_needs_a_base_snapshot(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ValueError, match="base_snapshot_id"):
                client.change_sets.handle("CHG-1").duplicate(target_network_id="202")
        assert recorder.requests == []

    def test_naming_its_own_network_as_the_target_needs_no_base_snapshot(
        self, recorder: Recorder
    ) -> None:
        recorder.add("POST", CS, json_response({"id": "CHG-3"}))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").duplicate(target_network_id="101")
        assert recorder.query_for() == {"action": ["duplicate"], "targetNetworkId": ["101"]}

    def test_stopping_a_collector_task_sends_the_chosen_action(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/collector-tasks/88", json_response({}))
        with make_client(recorder) as client:
            client.collector_tasks.stop_collector_task(task_id="88", action="CANCEL")
        assert recorder.query_for() == {"action": ["CANCEL"]}

    def test_a_copy_in_the_same_network_sends_no_target(self, recorder: Recorder) -> None:
        recorder.add("POST", CS, json_response({"id": "CHG-3"}))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").duplicate(name="copy")
        assert recorder.query_for() == {"action": ["duplicate"]}
        assert recorder.body_for() == {"targetName": "copy"}

    def test_a_draft_configuration_goes_up_as_plain_text(self, recorder: Recorder) -> None:
        path = f"{CS}/draft/devices/edge-1/config"
        recorder.add("PUT", path, httpx.Response(204))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").save_draft_device_config(
                "edge-1", "hostname edge-1\n"
            )
        request = recorder.requests[0]
        assert request.content == b"hostname edge-1\n"
        assert request.headers["content-type"].startswith("text/plain")

    def test_a_configuration_over_two_megabytes_is_refused_before_sending(
        self, recorder: Recorder
    ) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ValueError, match="at most"):
                client.change_sets.handle("CHG-1").save_draft_device_config(
                    "edge-1", "x" * (2 * 1024 * 1024 + 1)
                )
        assert recorder.requests == []

    def test_a_preview_asks_for_text_and_stores_nothing(self, recorder: Recorder) -> None:
        recorder.add(
            "POST", f"{CS}/devices/edge-1/config", text_response("hostname edge-1\n shutdown\n")
        )
        with make_client(recorder) as client:
            text = client.change_sets.handle("CHG-1").preview_device_config("edge-1", "shutdown")
        request = recorder.requests[0]
        assert request.url.params.get("action") == "preview"
        assert request.headers["accept"] == "text/plain"
        assert text.startswith("hostname")


class TestAssistsAndSchema:
    @pytest.mark.parametrize(
        ("method", "args", "path", "body"),
        [
            (
                "generate_nqe_query",
                ("list devices",),
                "/api/nqe/query-assists",
                {"prompt": "list devices"},
            ),
            (
                "summarize_nqe_query",
                ("select 1",),
                "/api/nqe/summary-assists",
                {"query": "select 1"},
            ),
            (
                "ask_nqe_docs",
                ("what is a join",),
                "/api/nqe/doc-assists",
                {"question": "what is a join"},
            ),
            (
                "suggest_nqe_doc_followups",
                ("5", "a join is"),
                "/api/nqe/doc-assists/5/followup",
                {"answer": "a join is"},
            ),
        ],
    )
    def test_each_assist_posts_its_documented_body(
        self,
        recorder: Recorder,
        method: str,
        args: tuple[str, ...],
        path: str,
        body: dict[str, Any],
    ) -> None:
        recorder.add("POST", path, json_response({"id": "1"}))
        with make_client(recorder) as client:
            getattr(client.ai, method)(*args)
        assert recorder.body_for() == body

    def test_the_schema_is_read_at_a_named_snapshot(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/nqe/schema", json_response({"type": "Record", "fields": []}))
        with make_client(recorder) as client:
            schema = client.nqe.schema("7")
        assert recorder.query_for() == {"snapshotId": ["7"]}
        assert schema.type == "Record"

    def test_run_metrics_use_the_from_parameter(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/nqe-run-metrics", json_response([]))
        with make_client(recorder) as client:
            client.nqe.run_metrics(from_="2026-01-01T00:00:00Z", limit=3)
        assert recorder.query_for() == {"from": ["2026-01-01T00:00:00Z"], "limit": ["3"]}

    def test_a_query_source_needs_its_commit(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/nqe/queries/FQ_1/source-code", json_response({"sourceCode": "x"}))
        with make_client(recorder) as client:
            client.nqe.repo.source_by_id("FQ_1", "a" * 40)
        assert recorder.query_for() == {"commitId": ["a" * 40]}

    def test_deployment_artifacts_list_by_type(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/deployment-artifacts",
            json_response(
                [
                    {
                        "id": "a1",
                        "name": "appliance",
                        "deploymentTypes": ["VMWARE_HYPERVISOR"],
                        "workflow": "INSTALLATION",
                        "versions": [],
                    }
                ]
            ),
        )
        with make_client(recorder) as client:
            rows = client.client_software.deployment_artifacts(type="OVA")
        assert recorder.query_for() == {"type": ["OVA"]}
        assert rows[0].id == "a1"


JOB = "/api/jobs/active"


def job_row(snapshot_id: int | None, link: str, total: int = 1) -> dict[str, Any]:
    row: dict[str, Any] = {
        "jobType": "NQE",
        "orgName": "org",
        "networkId": 101,
        "creationTime": "2026-10-20T00:00:00Z",
        "totalCount": total,
        "cancelLink": link,
    }
    if snapshot_id is not None:
        row["snapshotId"] = snapshot_id
    return row


class TestCancellingASnapshotsJobs:
    def test_only_that_snapshots_rows_are_cancelled(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            JOB,
            json_response(
                [job_row(7, "AAA"), job_row(8, "BBB"), job_row(None, "CCC"), job_row(7, "DDD")]
            ),
        )
        recorder.add("DELETE", "/api/jobs/AAA", httpx.Response(200))
        recorder.add("DELETE", "/api/jobs/DDD", httpx.Response(200))
        with make_client(recorder) as client:
            result = client.snapshots.cancel_jobs("7")
        deleted = [r.url.path for r in recorder.requests if r.method == "DELETE"]
        assert deleted == ["/api/jobs/AAA", "/api/jobs/DDD"]
        assert (result.matched, result.canceled, result.already_done) == (2, 2, 0)

    def test_the_cancel_link_goes_in_the_path_as_forward_gave_it(self, recorder: Recorder) -> None:
        recorder.add("GET", JOB, json_response([job_row(7, "ab%2Bcd%3D%3D")]))
        recorder.add("DELETE", "/api/jobs/ab+cd==", httpx.Response(200))
        with make_client(recorder) as client:
            client.snapshots.cancel_jobs("7")
        assert recorder.requests[-1].url.raw_path == b"/api/jobs/ab%2Bcd%3D%3D"

    def test_a_single_job_that_already_finished_is_counted_not_raised(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", JOB, json_response([job_row(7, "AAA"), job_row(7, "BBB")]))
        recorder.add("DELETE", "/api/jobs/AAA", httpx.Response(404, json={"message": "gone"}))
        recorder.add("DELETE", "/api/jobs/BBB", httpx.Response(200))
        with make_client(recorder) as client:
            result = client.snapshots.cancel_jobs("7")
        assert (result.matched, result.canceled, result.already_done) == (2, 1, 1)

    def test_a_group_forward_answers_404_for_is_not_assumed_done(self, recorder: Recorder) -> None:
        recorder.add("GET", JOB, json_response([job_row(7, "AAA", total=3)]))
        recorder.add("DELETE", "/api/jobs/AAA", httpx.Response(404, json={"message": "gone"}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError):
                client.snapshots.cancel_jobs("7")

    def test_nothing_matching_cancels_nothing(self, recorder: Recorder) -> None:
        recorder.add("GET", JOB, json_response([job_row(8, "BBB")]))
        with make_client(recorder) as client:
            result = client.snapshots.cancel_jobs("7")
        assert (result.matched, result.canceled) == (0, 0)
        assert recorder.count("DELETE", "/api/jobs/BBB") == 0
