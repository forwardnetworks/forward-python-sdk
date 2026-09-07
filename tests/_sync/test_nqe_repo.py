# Generated from tests/_async/test_nqe_repo.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Publishing queries to the library.

These use unpublished endpoints, so the response-shape tolerance is deliberate
and worth testing: Forward has been observed answering these in more than one
shape.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardConflictError, ForwardNotFoundError
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio

QUERIES = "/api/nqe/repos/org/commits/head/queries"
CHANGES = "/api/users/current/nqe/changes"
COMMITS = "/api/nqe/repos/org/commits"
HEAD = "/api/nqe/repos/org/commits/head"


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


class TestReading:
    def test_queries_wrapped_in_an_object(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        with make_client(recorder) as client:
            queries = client.nqe.repo.queries()

        assert queries[0].query_id == "FQ_1"
        assert queries[0].commit_id == "c1"

    def test_queries_returned_as_a_bare_list(self, recorder: Recorder) -> None:
        """Forward has answered this endpoint both ways."""
        recorder.add("GET", QUERIES, json_response([{"queryId": "FQ_1", "path": "/A"}]))
        with make_client(recorder) as client:
            assert (client.nqe.repo.queries())[0].path == "/A"

    def test_single_query_returned_bare(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queryId": "FQ_1", "path": "/A"}))
        with make_client(recorder) as client:
            queries = client.nqe.repo.queries(path="/A")

        assert len(queries) == 1
        assert queries[0].query_id == "FQ_1"
        assert recorder.query_for()["path"] == ["/A"]

    def test_head_commit_from_either_field(self, recorder: Recorder) -> None:
        recorder.add("GET", HEAD, json_response({"commitId": "abc"}))
        with make_client(recorder) as client:
            assert client.nqe.repo.head_commit_id() == "abc"

    def test_head_commit_as_a_bare_string(self, recorder: Recorder) -> None:
        recorder.add("GET", HEAD, json_response("abc"))
        with make_client(recorder) as client:
            assert client.nqe.repo.head_commit_id() == "abc"

    def test_find_missing_path(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no query at"):
                client.nqe.repo.find("/Nope")

    def test_drafts_listing(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", CHANGES, json_response({"changes": [{"path": "/A", "action": "addQuery"}]})
        )
        with make_client(recorder) as client:
            drafts = client.nqe.repo.drafts()

        assert drafts[0].path == "/A"
        assert drafts[0].action == "addQuery"


class TestPublishing:
    def test_publish_stages_adds_and_edits_then_commits(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "new-commit"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"/A": "source a", "/B": "source b"}, title="Update")

        assert set(report.committed_paths) == {"/A", "/B"}
        assert report.commit_id == "new-commit"

        staged = [r for r in recorder.requests if r.url.path == CHANGES]
        actions = [r.url.params.get("action") for r in staged]
        # /A exists so it is an edit; /B is new so it is an add.
        assert actions == ["editQuery", "addQuery"]

    def test_edit_records_the_version_it_is_based_on(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c2"}))

        with make_client(recorder) as client:
            client.nqe.repo.publish({"/A": "new source"}, title="Update")

        staged = next(r for r in recorder.requests if r.url.path == CHANGES)
        assert json.loads(staged.content)["basis"] == {"queryId": "FQ_1", "commitId": "c1"}

    def test_paths_gain_a_leading_slash(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"MyOrg/Q": "source"}, title="t")

        assert report.committed_paths == ("/MyOrg/Q",)

    def test_unchanged_paths_are_skipped_not_fatal(self, recorder: Recorder) -> None:
        """Publishing a directory where some files are identical is routine."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add(
            "POST",
            COMMITS,
            error_response(
                409,
                "User has no changes at the following paths: /B",
                reason="INVALID_CHANGE_PATH",
            ),
            json_response({}),
        )
        recorder.add("GET", HEAD, json_response({"id": "c2"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"/A": "source a", "/B": "source b"}, title="Update")

        assert report.committed_paths == ("/A",)
        assert report.skipped_paths == ("/B",)
        assert recorder.count("POST", COMMITS) == 2

    def test_commit_with_nothing_changed_is_not_an_error(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            COMMITS,
            error_response(
                409,
                "User has no changes at the following paths: /A, /B",
                reason="INVALID_CHANGE_PATH",
            ),
        )
        with make_client(recorder) as client:
            report = client.nqe.repo.commit(["/A", "/B"], title="No-op")

        assert report.committed_paths == ()
        assert report.skipped_paths == ("/A", "/B")
        assert not report.changed

    def test_an_unrelated_conflict_is_raised(self, recorder: Recorder) -> None:
        recorder.add("POST", COMMITS, error_response(409, "someone else committed first"))
        with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="someone else"):
                client.nqe.repo.commit(["/A"], title="t")

    def test_dry_run_failure_prevents_the_commit(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("DELETE", CHANGES, json_response({}))
        recorder.add(
            "POST",
            COMMITS,
            json_response({"newErrors": [{"message": "query no longer compiles"}]}),
        )

        with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="new error"):
                client.nqe.repo.publish({"/A": "broken"}, title="t", dry_run_snapshot_id="9")

        dry_run = next(
            r for r in recorder.requests if r.url.path == COMMITS and r.url.params.get("dryRun")
        )
        assert dry_run.url.params["snapshotId"] == "9"
        # The failed attempt must not leave drafts staged behind.
        assert recorder.count("DELETE", CHANGES) == 1

    def test_staging_failure_discards_earlier_drafts(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add(
            "POST",
            CHANGES,
            json_response({}),
            error_response(400, "invalid query source"),
        )
        recorder.add("DELETE", CHANGES, json_response({}))

        with make_client(recorder) as client:
            with pytest.raises(Exception, match="invalid query source"):
                client.nqe.repo.publish({"/A": "ok", "/B": "bad"}, title="t")

        assert recorder.count("DELETE", CHANGES) == 1

    def test_cleanup_can_be_turned_off(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, error_response(400, "nope"))

        with make_client(recorder) as client, pytest.raises(Exception, match="nope"):
            client.nqe.repo.publish({"/A": "x"}, title="t", discard_on_failure=False)

        assert recorder.count("DELETE", CHANGES) == 0

    def test_writes_invalidate_the_cached_index(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))

        with make_client(recorder) as client:
            client.nqe.repo.index()
            client.nqe.repo.stage_add("/A", "source")
            client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2
