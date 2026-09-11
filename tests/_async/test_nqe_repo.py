"""Publishing queries to the library.

These use unpublished endpoints, so the response-shape tolerance is deliberate
and worth testing: Forward has been observed answering these in more than one
shape.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import (
    ForwardConfigurationError,
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
COMMIT = "a" * 40
AT_COMMIT = f"/api/nqe/repos/org/commits/{COMMIT}/queries"


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
    async def test_queries_wrapped_in_an_object(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            QUERIES,
            json_response(
                {"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommit": {"id": "c1"}}]}
            ),
        )
        async with make_client(recorder) as client:
            queries = await client.nqe.repo.queries()

        assert queries[0].query_id == "FQ_1"
        assert queries[0].commit_id == "c1"

    async def test_flat_commit_id_is_also_understood(self, recorder: Recorder) -> None:
        """Integrations synthesize the flat key when normalizing; accept both."""
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        async with make_client(recorder) as client:
            assert (await client.nqe.repo.queries())[0].commit_id == "c1"

    async def test_queries_returned_as_a_bare_list(self, recorder: Recorder) -> None:
        """Forward has answered this endpoint both ways."""
        recorder.add("GET", QUERIES, json_response([{"queryId": "FQ_1", "path": "/A"}]))
        async with make_client(recorder) as client:
            assert (await client.nqe.repo.queries())[0].path == "/A"

    async def test_single_query_returned_bare(self, recorder: Recorder) -> None:
        recorder.add("GET", HEAD, json_response({"id": COMMIT}))
        recorder.add("GET", AT_COMMIT, json_response({"queryId": "FQ_1", "path": "/A"}))
        async with make_client(recorder) as client:
            queries = await client.nqe.repo.queries(path="/A")

        assert len(queries) == 1
        assert queries[0].query_id == "FQ_1"
        assert recorder.query_for()["path"] == ["/A"]

    async def test_dry_run_drops_unchanged_paths_like_commit_does(self, recorder: Recorder) -> None:
        """A dry run is the same request with a flag, refused for the same reason.

        Without this, publish(dry_run_snapshot_id=...) raised on an unchanged
        corpus where publish() alone reported it, so the documented
        recommendation and the no-op case could not be used together.
        """
        recorder.add(
            "POST",
            COMMITS,
            error_response(
                409,
                "User has no changes at the following paths: /A/q1, /A/q2.",
                reason="INVALID_CHANGE_PATH",
            ),
        )
        async with make_client(recorder) as client:
            report = await client.nqe.repo.dry_run(["/A/q1", "/A/q2"], snapshot_id="663")

        assert report == {}
        assert recorder.count("POST", COMMITS) == 1

    async def test_dry_run_retries_with_the_changed_subset(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            COMMITS,
            error_response(
                409,
                "User has no changes at the following paths: /A/q1.",
                reason="INVALID_CHANGE_PATH",
            ),
            json_response({"newErrors": []}),
        )
        async with make_client(recorder) as client:
            report = await client.nqe.repo.dry_run(["/A/q1", "/A/q2"], snapshot_id="663")

        assert report == {"newErrors": []}
        assert recorder.body_for()["paths"] == ["/A/q2"]

    async def test_filtering_at_head_resolves_the_commit_first(self, recorder: Recorder) -> None:
        """Forward ignores path and source at head instead of refusing them.

        The listing comes back whole and sourceless, so a path filter looks
        applied and a source audit reads every query as source-unavailable and
        passes vacuously. Resolving head to its commit makes both arguments mean
        what they say.
        """
        recorder.add("GET", HEAD, json_response({"id": COMMIT}))
        recorder.add("GET", AT_COMMIT, json_response({"queryId": "FQ_1", "path": "/A", "src": "x"}))
        async with make_client(recorder) as client:
            found = await client.nqe.repo.queries(path="/A", with_source=True)

        assert recorder.count("GET", QUERIES) == 0
        assert recorder.count("GET", AT_COMMIT) == 1
        assert [q.path for q in found] == ["/A"]

    async def test_listing_the_library_still_asks_head_directly(self, recorder: Recorder) -> None:
        """Enumerating needs no commit, so it costs no extra request."""
        recorder.add("GET", QUERIES, json_response({"queries": [{"path": "/A"}]}))
        async with make_client(recorder) as client:
            assert len(await client.nqe.repo.queries()) == 1

        assert recorder.count("GET", HEAD) == 0

    async def test_source_without_a_path_is_refused_locally(self, recorder: Recorder) -> None:
        """Forward will not stream the whole library's text.

        It answers with a message naming the parameter combination rather than
        the reason, so the request is refused here with one that explains it.
        """
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="one query at a time"):
                await client.nqe.repo.queries(with_source=True)

        assert recorder.count("GET", QUERIES) == 0
        assert recorder.count("GET", HEAD) == 0

    async def test_filtering_needs_a_commit_to_resolve_to(self, recorder: Recorder) -> None:
        """An empty repository has no commit, which is not an empty result."""
        recorder.add("GET", HEAD, json_response({}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no commit"):
                await client.nqe.repo.queries(path="/A")

    async def test_an_explicit_commit_is_used_as_given(self, recorder: Recorder) -> None:
        recorder.add("GET", AT_COMMIT, json_response({"queries": [{"path": "/A"}]}))
        async with make_client(recorder) as client:
            await client.nqe.repo.queries(commit_id=COMMIT, path="/A", with_source=True)

        assert recorder.count("GET", HEAD) == 0

    async def test_head_commit_from_either_field(self, recorder: Recorder) -> None:
        recorder.add("GET", HEAD, json_response({"commitId": "abc"}))
        async with make_client(recorder) as client:
            assert await client.nqe.repo.head_commit_id() == "abc"

    async def test_head_commit_as_a_bare_string(self, recorder: Recorder) -> None:
        recorder.add("GET", HEAD, json_response("abc"))
        async with make_client(recorder) as client:
            assert await client.nqe.repo.head_commit_id() == "abc"

    async def test_find_missing_path(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no query at"):
                await client.nqe.repo.find("/Nope")

    async def test_source_pins_the_commit_before_asking(self, recorder: Recorder) -> None:
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
        async with make_client(recorder) as client:
            assert await client.nqe.repo.source("/A") == "foreach x"

        pinned = recorder.requests[-1]
        assert commit in pinned.url.path, "the source lookup must pin the commit"
        assert pinned.url.params["with"] == "sourceCode"
        assert pinned.url.params["path"] == "/A"

    async def test_source_accepts_a_path_without_a_leading_slash(self, recorder: Recorder) -> None:
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
        async with make_client(recorder) as client:
            assert await client.nqe.repo.source("A") == "q"

    async def test_source_is_loud_when_the_commit_carries_none(self, recorder: Recorder) -> None:
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
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no source"):
                await client.nqe.repo.source("/A")

    async def test_source_of_an_uncommitted_query(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", QUERIES, json_response({"queries": [{"queryId": "FQ_1", "path": "/A"}]})
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no committed version"):
                await client.nqe.repo.source("/A")

    async def test_a_failed_lookup_is_not_reported_as_a_missing_query(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Failing to ask is not evidence about what is published."""
        recorder.add("GET", QUERIES, error_response(502, "gateway"))
        async with make_client(recorder, retries=0) as client:
            with pytest.raises(ForwardServerError):
                await client.nqe.repo.source("/A")

    async def test_source_of_a_missing_query(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no query at"):
                await client.nqe.repo.source("/Nope")

    async def test_directory_is_created_with_the_trailing_slash_forward_wants(
        self, recorder: Recorder
    ) -> None:
        recorder.add("POST", CHANGES, json_response({}))
        async with make_client(recorder) as client:
            await client.nqe.repo.stage_directory("/MyOrg")

        assert recorder.query_for()["path"] == ["/MyOrg/"]
        assert recorder.query_for()["action"] == ["addDir"]

    async def test_discard_strips_the_trailing_slash(self, recorder: Recorder) -> None:
        """Forward wants the slash to create a directory and not to discard one.

        Passing the path back as the draft listing reports it fails with a
        message about access settings, which strands every directory a failed
        publish created.
        """
        recorder.add("DELETE", CHANGES, json_response({}))
        async with make_client(recorder) as client:
            await client.nqe.repo.discard("/MyOrg/")

        assert recorder.query_for()["path"] == ["/MyOrg"]

    async def test_publish_creates_missing_directories_first(self, recorder: Recorder) -> None:
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

        async with make_client(recorder) as client:
            await client.nqe.repo.publish({"/BrandNew/Deep/q": "src"}, title="t")

        staged = [r for r in recorder.requests if r.method == "POST" and r.url.path == CHANGES]
        actions = [(r.url.params.get("action"), r.url.params.get("path")) for r in staged]
        # Parents before children, then the query itself.
        assert actions == [
            ("addDir", "/BrandNew/"),
            ("addDir", "/BrandNew/Deep/"),
            ("addQuery", "/BrandNew/Deep/q"),
        ]

    async def test_publish_does_not_recreate_existing_directories(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/Existing/q"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        async with make_client(recorder) as client:
            await client.nqe.repo.publish({"/Existing/another": "src"}, title="t")

        staged = [r for r in recorder.requests if r.method == "POST" and r.url.path == CHANGES]
        assert [r.url.params.get("action") for r in staged] == ["addQuery"]

    async def test_drafts_listing(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", CHANGES, json_response({"changes": [{"path": "/A", "action": "addQuery"}]})
        )
        async with make_client(recorder) as client:
            drafts = await client.nqe.repo.drafts()

        assert drafts[0].path == "/A"
        assert drafts[0].action == "addQuery"


class TestPublishing:
    async def test_publish_stages_adds_and_edits_then_commits(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "new-commit"}))

        async with make_client(recorder) as client:
            report = await client.nqe.repo.publish(
                {"/A": "source a", "/B": "source b"}, title="Update"
            )

        assert set(report.committed_paths) == {"/A", "/B"}
        assert report.commit_id == "new-commit"

        staged = [r for r in recorder.requests if r.url.path == CHANGES and r.method == "POST"]
        actions = [r.url.params.get("action") for r in staged]
        # /A exists so it is an edit; /B is new so it is an add.
        assert actions == ["editQuery", "addQuery"]

    async def test_edit_records_the_version_it_is_based_on(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add(
            "GET",
            QUERIES,
            json_response({"queries": [{"queryId": "FQ_1", "path": "/A", "lastCommitId": "c1"}]}),
        )
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c2"}))

        async with make_client(recorder) as client:
            await client.nqe.repo.publish({"/A": "new source"}, title="Update")

        staged = next(r for r in recorder.requests if r.url.path == CHANGES and r.method == "POST")
        assert json.loads(staged.content)["basis"] == {"queryId": "FQ_1", "commitId": "c1"}

    async def test_paths_gain_a_leading_slash(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        async with make_client(recorder) as client:
            report = await client.nqe.repo.publish({"MyOrg/Q": "source"}, title="t")

        assert report.committed_paths == ("/MyOrg/Q",)

    async def test_publish_refuses_to_commit_over_an_existing_draft(
        self, recorder: Recorder
    ) -> None:
        """A commit names paths, so someone else's draft would be published too."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add(
            "GET", CHANGES, json_response({"changes": [{"path": "/B", "action": "editQuery"}]})
        )
        recorder.default = lambda request: pytest.fail(
            f"publish should have stopped before {request.method} {request.url.path}"
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="already have uncommitted"):
                await client.nqe.repo.publish({"/A": "a", "/B": "b"}, title="t")

        assert recorder.count("POST", CHANGES) == 0
        assert recorder.count("POST", COMMITS) == 0

    async def test_publish_can_be_told_to_overwrite_drafts(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("POST", COMMITS, json_response({}))
        recorder.add("GET", HEAD, json_response({"id": "c"}))

        async with make_client(recorder) as client:
            report = await client.nqe.repo.publish({"/A": "a"}, title="t", overwrite_drafts=True)

        assert report.committed_paths == ("/A",)
        # The draft listing is not even consulted when overwriting.
        assert recorder.count("GET", CHANGES) == 0

    async def test_strip_and_retry_reads_the_body_not_the_display_string(
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

        async with make_client(recorder) as client:
            report = await client.nqe.repo.publish({"/A": "a", "/B": "b", "/C": "c"}, title="t")

        assert report.committed_paths == ("/A",)
        assert report.skipped_paths == ("/B", "/C")

    async def test_unidentifiable_conflict_is_raised_rather_than_half_stripped(
        self, recorder: Recorder
    ) -> None:
        """If the paths cannot be read, fail honestly instead of retrying blind."""
        recorder.add(
            "POST",
            COMMITS,
            httpx.Response(409, content=b"<html>gateway</html>"),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError):
                await client.nqe.repo.commit(["/A"], title="t")

        assert recorder.count("POST", COMMITS) == 1

    async def test_unchanged_paths_are_skipped_not_fatal(self, recorder: Recorder) -> None:
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

        async with make_client(recorder) as client:
            report = await client.nqe.repo.publish(
                {"/A": "source a", "/B": "source b"}, title="Update"
            )

        assert report.committed_paths == ("/A",)
        assert report.skipped_paths == ("/B",)
        assert recorder.count("POST", COMMITS) == 2

    async def test_commit_with_nothing_changed_is_not_an_error(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            COMMITS,
            error_response(
                409,
                "User has no changes at the following paths: /A, /B",
                reason="INVALID_CHANGE_PATH",
            ),
        )
        async with make_client(recorder) as client:
            report = await client.nqe.repo.commit(["/A", "/B"], title="No-op")

        assert report.committed_paths == ()
        assert report.skipped_paths == ("/A", "/B")
        assert not report.changed

    async def test_an_unrelated_conflict_is_raised(self, recorder: Recorder) -> None:
        recorder.add("POST", COMMITS, error_response(409, "someone else committed first"))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="someone else"):
                await client.nqe.repo.commit(["/A"], title="t")

    async def test_dry_run_failure_prevents_the_commit(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))
        recorder.add("DELETE", CHANGES, json_response({}))
        recorder.add(
            "POST",
            COMMITS,
            json_response({"newErrors": [{"message": "query no longer compiles"}]}),
        )

        async with make_client(recorder) as client:
            with pytest.raises(ForwardConflictError, match="new error"):
                await client.nqe.repo.publish({"/A": "broken"}, title="t", dry_run_snapshot_id="9")

        dry_run = next(
            r for r in recorder.requests if r.url.path == COMMITS and r.url.params.get("dryRun")
        )
        assert dry_run.url.params["snapshotId"] == "9"
        # The failed attempt must not leave drafts staged behind.
        assert recorder.count("DELETE", CHANGES) == 1

    async def test_staging_failure_discards_earlier_drafts(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add(
            "POST",
            CHANGES,
            json_response({}),
            error_response(400, "invalid query source"),
        )
        recorder.add("DELETE", CHANGES, json_response({}))

        async with make_client(recorder) as client:
            with pytest.raises(Exception, match="invalid query source"):
                await client.nqe.repo.publish({"/A": "ok", "/B": "bad"}, title="t")

        assert recorder.count("DELETE", CHANGES) == 1

    async def test_cleanup_can_be_turned_off(self, recorder: Recorder) -> None:
        recorder.add("GET", CHANGES, json_response(NO_DRAFTS))
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, error_response(400, "nope"))

        async with make_client(recorder) as client:
            with pytest.raises(Exception, match="nope"):
                await client.nqe.repo.publish({"/A": "x"}, title="t", discard_on_failure=False)

        assert recorder.count("DELETE", CHANGES) == 0

    async def test_index_is_refetched_once_the_cache_expires(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """The library changes when someone else publishes, so the cache expires."""
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        async with make_client(recorder, cache_ttl=30.0) as client:
            await client.nqe.repo.index()
            await client.nqe.repo.index()
            assert recorder.count("GET", QUERIES) == 1
            await asyncio.sleep(31)  # advances the fake clock
            await client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2

    async def test_cache_can_be_disabled(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        async with make_client(recorder, cache_ttl=0) as client:
            await client.nqe.repo.index()
            await client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2

    async def test_writes_invalidate_the_cached_index(self, recorder: Recorder) -> None:
        recorder.add("GET", QUERIES, json_response({"queries": []}))
        recorder.add("POST", CHANGES, json_response({}))

        async with make_client(recorder) as client:
            await client.nqe.repo.index()
            await client.nqe.repo.stage_add("/A", "source")
            await client.nqe.repo.index()

        assert recorder.count("GET", QUERIES) == 2
