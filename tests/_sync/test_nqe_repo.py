# Generated from tests/_async/test_nqe_repo.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Publishing queries to the library.

These use unpublished endpoints, so the response-shape tolerance is deliberate
and worth testing: Forward has been observed answering these in more than one
shape.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import (
    ForwardConflictError,
    ForwardNotFoundError,
    ForwardServerError,
)
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio

QUERIES = "/api/nqe/repos/org/commits/head/queries"
CHANGES = "/api/users/current/nqe/changes"
COMMITS = "/api/nqe/repos/org/commits"
HEAD = "/api/nqe/repos/org/commits/head"
NO_DRAFTS: dict[str, Any] = {"changes": []}


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
            json_response(
                {"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommit": {"id": "c1"}}]}
            ),
        )
        with make_client(recorder) as client:
            queries = client.nqe.repo.queries()

        assert queries[0].query_id == "FQ_1"
        assert queries[0].commit_id == "c1"

    def test_flat_commit_id_is_also_understood(self, recorder: Recorder) -> None:
        """Integrations synthesize the flat key when normalizing; accept both."""
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        with make_client(recorder) as client:
            assert (client.nqe.repo.queries())[0].commit_id == "c1"

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

    def test_source_pins_the_commit_before_asking(self, recorder: Recorder) -> None:
        """Forward honours path and with=sourceCode only against a real commit.

        Asked at head it ignores both, returns the whole library without source,
        and a caller filtering by path would think the filter had applied.
        """
        commit = "c" * 40
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": commit}]}),
        )
        recorder.add(
            "GET",
            f"/api/nqe/repos/org/commits/{commit}/queries",
            json_response(
                {"queries": [{"queryId": "FQ_1", "path": "/A", "sourceCode": "foreach x"}]}
            ),
        )
        with make_client(recorder) as client:
            assert client.nqe.repo.source("/A") == "foreach x"

        pinned = recorder.requests[-1]
        assert commit in pinned.url.path, "the source lookup must pin the commit"
        assert pinned.url.params["with"] == "sourceCode"
        assert pinned.url.params["path"] == "/A"

    def test_source_accepts_a_path_without_a_leading_slash(self, recorder: Recorder) -> None:
        commit = "c" * 40
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": commit}]}),
        )
        recorder.add(
            "GET",
            f"/api/nqe/repos/org/commits/{commit}/queries",
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "sourceCode": "q"}]}),
        )
        with make_client(recorder) as client:
            assert client.nqe.repo.source("A") == "q"

    def test_source_is_loud_when_the_commit_carries_none(self, recorder: Recorder) -> None:
        """Silently returning None would surface later as an empty comparison."""
        commit = "c" * 40
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": commit}]}),
        )
        recorder.add(
            "GET",
            f"/api/nqe/repos/org/commits/{commit}/queries",
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A"}]}),
        )
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no source"):
                client.nqe.repo.source("/A")

    def test_source_of_an_uncommitted_query(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", QUERIES, json_response({"queries": [{"queryId": "FQ_1", "path": "/A"}]})
        )
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no committed version"):
                client.nqe.repo.source("/A")

    def test_a_failed_lookup_is_not_reported_as_a_missing_query(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Failing to ask is not evidence about what is published."""
        recorder.add("GET", QUERIES, error_response(502, "gateway"))
        with make_client(recorder, retries=0) as client:
            with pytest.raises(ForwardServerError):
                client.nqe.repo.source("/A")

    def test_source_of_a_missing_query(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no query at"):
                client.nqe.repo.source("/Nope")

    def test_directory_is_created_with_the_trailing_slash_forward_wants(
        self, recorder: Recorder
    ) -> None:
        recorder.add("POST", CHANGES, json_response({}))
        with make_client(recorder) as client:
            client.nqe.repo.stage_directory("/MyOrg")

        assert recorder.query_for()["path"] == ["/MyOrg/"]
        assert recorder.query_for()["action"] == ["addDir"]

    def test_discard_strips_the_trailing_slash(self, recorder: Recorder) -> None:
        """Forward wants the slash to create a directory and not to discard one.

        Passing the path back as the draft listing reports it fails with a
        message about access settings, which strands every directory a failed
        publish created.
        """
        recorder.add("DELETE", CHANGES, json_response({}))
        with make_client(recorder) as client:
            client.nqe.repo.discard("/MyOrg/")

        assert recorder.query_for()["path"] == ["/MyOrg"]

    def test_publish_creates_missing_directories_first(self, recorder: Recorder) -> None:
        """Forward refuses to stage a query whose directory does not exist."""
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/Existing/q"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        with make_client(recorder) as client:
            client.nqe.repo.publish({"/BrandNew/Deep/q": "src"}, title="t")

        staged = [r for r in recorder.requests if r.method == "POST" and r.url.path == CHANGES]
        actions = [(r.url.params.get("action"), r.url.params.get("path")) for r in staged]
        # Parents before children, then the query itself.
        assert actions == [
            ("addDir", "/BrandNew/"),
            ("addDir", "/BrandNew/Deep/"),
            ("addQuery", "/BrandNew/Deep/q"),
        ]

    def test_publish_does_not_recreate_existing_directories(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/Existing/q"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        with make_client(recorder) as client:
            client.nqe.repo.publish({"/Existing/another": "src"}, title="t")

        staged = [r for r in recorder.requests if r.method == "POST" and r.url.path == CHANGES]
        assert [r.url.params.get("action") for r in staged] == ["addQuery"]

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
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
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

        staged = [r for r in recorder.requests if r.url.path == CHANGES and r.method == "POST"]
        actions = [r.url.params.get("action") for r in staged]
        # /A exists so it is an edit; /B is new so it is an add.
        assert actions == ["editQuery", "addQuery"]

    def test_edit_records_the_version_it_is_based_on(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
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

        staged = next(r for r in recorder.requests if r.url.path == CHANGES and r.method == "POST")
        assert json.loads(staged.content)["basis"] == {"queryId": "FQ_1", "commitId": "c1"}

    def test_paths_gain_a_leading_slash(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"MyOrg/Q": "source"}, title="t")

        assert report.committed_paths == ("/MyOrg/Q",)

    def test_publish_refuses_to_commit_over_an_existing_draft(self, recorder: Recorder) -> None:
        """A commit names paths, so someone else's draft would be published too."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add(
            "GET", CHANGES, json_response({"changes": [{"path": "/B", "action": "editQuery"}]})
        )
        recorder.default = lambda request: pytest.fail(
            f"publish should have stopped before {request.method} {request.url.path}"
        )
        with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="already have uncommitted"):
                client.nqe.repo.publish({"/A": "a", "/B": "b"}, title="t")

        assert recorder.count("POST", CHANGES) == 0
        assert recorder.count("POST", COMMITS) == 0

    def test_publish_can_be_told_to_overwrite_drafts(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"/A": "a"}, title="t", overwrite_drafts=True)

        assert report.committed_paths == ("/A",)
        # The draft listing is not even consulted when overwriting.
        assert recorder.count("GET", CHANGES) == 0

    def test_strip_and_retry_reads_the_body_not_the_display_string(
        self, recorder: Recorder
    ) -> None:
        """An error envelope missing ErrorInfo's required fields must still parse.

        The unpublished commit endpoint is not obliged to send a full ErrorInfo.
        Falling back to str(error) embeds the raw JSON in formatting, so paths
        come back as fragments, only some get stripped, and the retry fails
        again on a 409 that looks like a different problem.
        """
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("GET", CHANGES, json_response({"changes": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add(
            "POST",
            COMMITS,
            # No apiUrl or httpMethod, so ErrorInfo will not validate.
            httpx.Response(
                409,
                json={
                    "reason": "INVALID_CHANGE_PATH",
                    "message": ("User has no changes at the following paths: /B, /C"),
                },
            ),
            json_response({}),
        )
        recorder.add("GET", HEAD, json_response({"id": "c2"}))

        with make_client(recorder) as client:
            report = client.nqe.repo.publish({"/A": "a", "/B": "b", "/C": "c"}, title="t")

        assert report.committed_paths == ("/A",)
        assert report.skipped_paths == ("/B", "/C")

    def test_unidentifiable_conflict_is_raised_rather_than_half_stripped(
        self, recorder: Recorder
    ) -> None:
        """If the paths cannot be read, fail honestly instead of retrying blind."""
        recorder.add(
            "POST",
            COMMITS,
            httpx.Response(409, content=b"<html>gateway</html>"),
        )
        with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError):
                client.nqe.repo.commit(["/A"], title="t")

        assert recorder.count("POST", COMMITS) == 1

    def test_unchanged_paths_are_skipped_not_fatal(self, recorder: Recorder) -> None:
        """Publishing a directory where some files are identical is routine."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("GET", CHANGES, json_response({"changes": []}))
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
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
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
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
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
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, error_response(400, "nope"))

        with make_client(recorder) as client:
            with pytest.raises(Exception, match="nope"):
                client.nqe.repo.publish({"/A": "x"}, title="t", discard_on_failure=False)

        assert recorder.count("DELETE", CHANGES) == 0

    def test_index_is_refetched_once_the_cache_expires(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """The library changes when someone else publishes, so the cache expires."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        with make_client(recorder, cache_ttl=30.0) as client:
            client.nqe.repo.index()
            client.nqe.repo.index()
            assert recorder.count("GET", QUERIES) == 1
            time.sleep(31)  # advances the fake clock
            client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2

    def test_cache_can_be_disabled(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        with make_client(recorder, cache_ttl=0) as client:
            client.nqe.repo.index()
            client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2

    def test_writes_invalidate_the_cached_index(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))

        with make_client(recorder) as client:
            client.nqe.repo.index()
            client.nqe.repo.stage_add("/A", "source")
            client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2
