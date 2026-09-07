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

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._ops import nqe_repo as ops
from forward_sdk.errors import ForwardConflictError, ForwardNotFoundError

__all__ = ["AsyncNqeRepository", "CommitReport", "DraftChange", "RepositoryQuery"]

#: Forward rejects a commit naming a path with no staged change, listing the
#: offending paths in the message. That happens routinely when a query's source
#: is unchanged, so the offending paths are dropped and the commit retried.
NO_CHANGES_PREFIX = "User has no changes at the following paths:"
INVALID_CHANGE_PATH = "INVALID_CHANGE_PATH"


@dataclass(frozen=True, slots=True)
class RepositoryQuery:
    """A query in the library."""

    query_id: str
    path: str
    commit_id: str | None = None
    intent: str | None = None
    repository: str = "org"
    source: str | None = None

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], *, repository: str = "org"
    ) -> RepositoryQuery:
        return cls(
            query_id=str(payload.get("queryId") or payload.get("id") or ""),
            path=str(payload.get("path") or ""),
            commit_id=_optional_str(payload.get("lastCommitId") or payload.get("commitId")),
            intent=_optional_str(payload.get("intent")),
            repository=str(payload.get("repository") or repository).lower(),
            source=_optional_str(payload.get("sourceCode")),
        )


@dataclass(frozen=True, slots=True)
class DraftChange:
    """One staged, uncommitted change."""

    path: str
    action: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DraftChange:
        return cls(
            path=str(payload.get("path") or ""),
            action=_optional_str(payload.get("action") or payload.get("type")),
        )


@dataclass(frozen=True, slots=True)
class CommitReport:
    """The outcome of publishing queries."""

    committed_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    commit_id: str | None = None
    dry_run: bool = False
    new_errors: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.committed_paths)


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _queries_from_payload(payload: Any, repository: str) -> list[RepositoryQuery]:
    """Read a query listing.

    Forward returns either a wrapped ``{"queries": [...]}`` object or a bare
    list, and a single-path lookup may return the query object on its own.
    """
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        rows = payload.get("queries")
        if rows is None:
            return [RepositoryQuery.from_payload(payload, repository=repository)]
    else:
        rows = payload
    return [
        RepositoryQuery.from_payload(row, repository=repository)
        for row in rows or []
        if isinstance(row, Mapping)
    ]


def _paths_without_changes(message: str) -> set[str]:
    """Pull the offending paths out of Forward's rejection message."""
    match = re.search(re.escape(NO_CHANGES_PREFIX) + r"\s*(?P<paths>.+)", message, re.DOTALL)
    if not match:
        return set()
    return {
        part.strip().strip("'\"")
        for part in re.split(r"[,\n]", match.group("paths"))
        if part.strip()
    }


class AsyncNqeRepository(AsyncService):
    """The NQE query library.

    Every method here uses unpublished endpoints; see the module docstring.
    """

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self._index_cache: dict[str, dict[str, RepositoryQuery]] = {}

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
        return _queries_from_payload(payload, repository)

    async def index(
        self, *, repository: str = "org", refresh: bool = False
    ) -> dict[str, RepositoryQuery]:
        """Map every query path to its entry.

        Cached for the client's lifetime, because resolving a batch of query
        paths otherwise re-fetches the whole library for each one. Any write
        through this object clears the cache.
        """
        if refresh or repository not in self._index_cache:
            entries = await self.queries(repository=repository)
            self._index_cache[repository] = {e.path: e for e in entries if e.path}
            self._transport.counters.increment("cache_misses")
        else:
            self._transport.counters.increment("cache_hits")
        return self._index_cache[repository]

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
        return _optional_str(data.get("id") or data.get("commitId"))

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
        """Return the paths Forward says have no staged change, if that is why it refused."""
        message = getattr(error.error_info, "message", "") or str(error)
        if error.reason != INVALID_CHANGE_PATH and NO_CHANGES_PREFIX not in message:
            return None
        named = _paths_without_changes(message)
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
        """
        index = await self.index(repository=repository, refresh=True)
        normalized = {
            (path if path.startswith("/") else "/" + path): source for path, source in files.items()
        }

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
