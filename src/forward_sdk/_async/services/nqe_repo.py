"""Reading and publishing NQE queries in the query library.

These endpoints are unpublished: absent from Forward's public API description,
so the SDK describes their shapes by hand. They are stable and safe to depend
on, and two Forward integrations already do. Shipping queries alongside the code
that consumes them has no published alternative.

Publishing is a staged workflow, mirroring version control: changes are staged
as drafts against the current user, optionally validated with a dry run, then
committed together.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._ops import nqe_repo as ops
from forward_sdk.errors import ForwardConflictError, ForwardNotFoundError
from forward_sdk.nqe.repository import (
    INVALID_CHANGE_PATH,
    NO_CHANGES_PREFIX,
    CommitReport,
    DraftChange,
    RepositoryQuery,
    message_of,
    optional_str,
    paths_without_changes,
    queries_from_payload,
)

__all__ = ["AsyncNqeRepository"]


class AsyncNqeRepository(AsyncService):
    """The NQE query library.

    Every method here uses unpublished endpoints; see the module docstring.
    """

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self._index_cache: dict[str, dict[str, RepositoryQuery]] = {}
        self._index_fetched_at: dict[str, float] = {}

    async def queries(
        self,
        *,
        repository: str = "org",
        commit_id: str = "head",
        path: str | None = None,
        with_source: bool = False,
    ) -> list[RepositoryQuery]:
        """List queries, optionally under one path and including source."""
        payload = await self._send_json(
            ops.list_queries(
                repository=repository,
                commit_id=commit_id,
                path=path,
                with_source=with_source,
            )
        )
        return queries_from_payload(payload, repository)

    async def index(
        self, *, repository: str = "org", refresh: bool = False
    ) -> dict[str, RepositoryQuery]:
        """Map every query path to its entry.

        Cached for the client's lifetime, because resolving a batch of query
        paths otherwise re-fetches the whole library for each one. Any write
        through this object clears the cache.
        """
        if refresh or self._index_is_stale(repository):
            entries = await self.queries(repository=repository)
            self._index_cache[repository] = {e.path: e for e in entries if e.path}
            self._index_fetched_at[repository] = time.monotonic()
            self._transport.counters.increment("cache_misses")
        else:
            self._transport.counters.increment("cache_hits")
        return self._index_cache[repository]

    def _index_is_stale(self, repository: str) -> bool:
        """Whether the cached index must be fetched again.

        The library changes when someone else publishes, so a long-lived client
        would otherwise resolve query paths against an index from hours ago.
        The client's ``cache_ttl`` bounds that; zero disables caching.
        """
        if repository not in self._index_cache:
            return True
        ttl = self._config.cache_ttl
        if ttl <= 0:
            return True
        fetched = self._index_fetched_at.get(repository)
        return fetched is None or (time.monotonic() - fetched) >= ttl

    async def find(self, path: str, *, repository: str = "org") -> RepositoryQuery:
        """Look up one query by its library path.

        Raises:
            ForwardNotFoundError: If no query exists at that path.
        """
        index = await self.index(repository=repository)
        entry = index.get(path if path.startswith("/") else "/" + path)
        if entry is None:
            raise ForwardNotFoundError(
                f"no query at {path!r} in the {repository} repository",
                status=404,
            )
        return entry

    async def head_commit_id(self) -> str | None:
        """The organization repository's current commit."""
        payload = await self._send_json(ops.head_commit())
        if isinstance(payload, str):
            return payload
        data = payload or {}
        return optional_str(data.get("id") or data.get("commitId"))

    async def history(self, query_id: str) -> list[dict[str, Any]]:
        """List the commits that touched a query."""
        payload = await self._send_json(ops.query_history(query_id=query_id))
        if isinstance(payload, list):
            return list(payload)
        return list((payload or {}).get("commits") or [])

    async def drafts(self) -> list[DraftChange]:
        """List the current user's staged, uncommitted changes."""
        payload = await self._send_json(ops.list_drafts())
        rows = payload if isinstance(payload, list) else (payload or {}).get("changes") or []
        return [DraftChange.from_payload(row) for row in rows if isinstance(row, Mapping)]

    async def stage_add(self, path: str, source: str) -> None:
        """Stage a new query."""
        await self._send_json(ops.stage_change(action="addQuery", path=path, source=source))
        self._invalidate()

    async def stage_edit(
        self, path: str, source: str, *, query_id: str, commit_id: str | None = None
    ) -> None:
        """Stage a change to an existing query.

        The basis identifies the version being edited, so Forward can reject an
        edit built on a version that has since moved on.
        """
        basis = {"queryId": query_id}
        if commit_id:
            basis["commitId"] = commit_id
        await self._send_json(
            ops.stage_change(action="editQuery", path=path, source=source, basis=basis)
        )
        self._invalidate()

    async def stage_directory(self, path: str) -> None:
        """Create a directory in the library."""
        await self._send_json(ops.stage_change(action="addDir", path=path))
        self._invalidate()

    async def discard(self, path: str) -> None:
        """Discard one staged change."""
        await self._send_json(ops.discard_change(path=path))
        self._invalidate()

    async def dry_run(
        self, paths: Sequence[str], *, snapshot_id: str | None = None
    ) -> dict[str, Any]:
        """Validate staged changes without committing them.

        Returns Forward's report, including any errors the changes would
        introduce and which existing queries depend on them.
        """
        payload = await self._send_json(
            ops.commit(paths=paths, title="", dry_run=True, snapshot_id=snapshot_id)
        )
        return dict(payload or {})

    async def commit(self, paths: Sequence[str], *, title: str, body: str = "") -> CommitReport:
        """Commit staged changes.

        Paths with nothing staged are dropped and the commit retried, because
        publishing a set of files where some are unchanged is the normal case,
        not an error.
        """
        requested = list(paths)
        try:
            await self._send_json(ops.commit(paths=requested, title=title, body=body))
        except ForwardConflictError as exc:
            unchanged = self._unchanged_paths(exc, requested)
            if unchanged is None:
                raise
            remaining = [p for p in requested if p not in unchanged]
            if not remaining:
                return CommitReport(skipped_paths=tuple(sorted(unchanged)))
            await self._send_json(ops.commit(paths=remaining, title=title, body=body))
            self._invalidate()
            return CommitReport(
                committed_paths=tuple(remaining),
                skipped_paths=tuple(sorted(unchanged)),
                commit_id=await self.head_commit_id(),
            )

        self._invalidate()
        return CommitReport(committed_paths=tuple(requested), commit_id=await self.head_commit_id())

    @staticmethod
    def _unchanged_paths(error: ForwardConflictError, requested: Sequence[str]) -> set[str] | None:
        """The paths Forward says have no staged change, if that is why it refused.

        The message comes from the parsed error body, or from the raw body
        re-read as JSON. Never from the exception's display string: that embeds
        the raw body inside formatting, so splitting it yields fragments like
        ``/a/b"}`` which match nothing, and the retry then strips only some of
        the named paths and fails again on a second 409 that looks like a
        different problem. This endpoint is unpublished, so its error envelope
        is the least guaranteed of any, and it is exactly where a parsed body
        cannot be relied on.

        Returns ``None`` when the paths cannot be identified, so the caller
        re-raises rather than retrying a partial strip.
        """
        message = message_of(error)
        if message is None:
            return None
        if error.reason != INVALID_CHANGE_PATH and NO_CHANGES_PREFIX not in message:
            return None
        named = paths_without_changes(message)
        if not named:
            return None
        matched = {path for path in requested if path in named}
        return matched or None

    async def publish(
        self,
        files: Mapping[str, str],
        *,
        title: str,
        body: str = "",
        repository: str = "org",
        dry_run_snapshot_id: str | None = None,
        discard_on_failure: bool = True,
        overwrite_drafts: bool = False,
    ) -> CommitReport:
        """Publish a set of queries to the library in one commit.

        Each path is staged as an addition or an edit depending on whether it
        already exists, optionally validated, then committed together.

        Args:
            files: Library path to query source. Paths gain a leading slash.
            dry_run_snapshot_id: Validate against this snapshot before
                committing. Recommended: it catches a query that no longer
                compiles before it reaches anyone else.
            discard_on_failure: Remove staged drafts if publishing fails, so a
                failed run does not leave half-staged changes behind.
            overwrite_drafts: Publish even when one of these paths already has
                an uncommitted draft. Off by default: a commit names paths, not
                changes, so someone's half-finished edit sitting on a path you
                are publishing would be committed along with yours, and neither
                of you would be told.

        Raises:
            ForwardConflictError: If a path already has a draft and
                ``overwrite_drafts`` is not set.
        """
        index = await self.index(repository=repository, refresh=True)
        normalized = {
            (path if path.startswith("/") else "/" + path): source for path, source in files.items()
        }

        if not overwrite_drafts:
            await self._refuse_existing_drafts(normalized)

        staged: list[str] = []
        try:
            for path, source in normalized.items():
                existing = index.get(path)
                if existing is None:
                    await self.stage_add(path, source)
                else:
                    await self.stage_edit(
                        path,
                        source,
                        query_id=existing.query_id,
                        commit_id=existing.commit_id,
                    )
                staged.append(path)

            if dry_run_snapshot_id is not None:
                report = await self.dry_run(staged, snapshot_id=dry_run_snapshot_id)
                errors = report.get("newErrors") or []
                if errors:
                    raise ForwardConflictError(
                        f"publishing {len(staged)} queries would introduce "
                        f"{len(errors)} new error(s); nothing was committed",
                        status=409,
                    )

            return await self.commit(staged, title=title, body=body)
        except Exception:
            if discard_on_failure:
                await self._discard_quietly(staged)
            raise

    async def _refuse_existing_drafts(self, paths: Mapping[str, str]) -> None:
        """Stop if any path already carries an uncommitted change.

        Committing publishes whatever is staged on the named paths, so an
        unrelated draft would be published as part of this commit with no
        signal to either party.
        """
        existing = {draft.path for draft in await self.drafts()}
        clashes = sorted(path for path in paths if path in existing)
        if clashes:
            raise ForwardConflictError(
                f"{len(clashes)} path(s) already have uncommitted changes: "
                f"{', '.join(clashes)}. Committing would publish those too. "
                "Discard them, or pass overwrite_drafts=True to publish anyway.",
                status=409,
            )

    async def _discard_quietly(self, paths: Sequence[str]) -> None:
        """Best-effort cleanup; never mask the failure that triggered it."""
        for path in paths:
            try:
                await self.discard(path)
            except Exception:
                # Cleanup is best effort; never mask the original failure.
                continue

    def _invalidate(self) -> None:
        self._index_cache.clear()
        self._index_fetched_at.clear()
