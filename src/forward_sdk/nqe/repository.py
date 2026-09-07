"""Types and parsing for the NQE query library.

Kept out of the service modules deliberately: those are duplicated into a
synchronous twin by ``scripts/unasync.py``, and a dataclass defined there would
become two distinct classes for the same thing, so an ``isinstance`` check would
fail depending on which client produced the value.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forward_sdk._generated import models as wire

__all__ = [
    "INVALID_CHANGE_PATH",
    "NO_CHANGES_PREFIX",
    "CommitReport",
    "DraftChange",
    "RepositoryQuery",
    "commit_id_of",
    "message_of",
    "optional_str",
    "paths_without_changes",
    "queries_from_payload",
]

#: Forward rejects a commit naming a path with no staged change, listing the
#: offending paths in the message. That happens routinely when a query's source
#: is unchanged, so the offending paths are dropped and the commit retried.
NO_CHANGES_PREFIX = "User has no changes at the following paths:"
INVALID_CHANGE_PATH = "INVALID_CHANGE_PATH"


@dataclass(frozen=True, slots=True)
class RepositoryQuery:
    """A query in the library.

    Attributes:
        query_id: Forward's identifier for the query.
        path: Its path in the library.
        commit_id: The commit this entry came from, when Forward reported one.
        intent: The query's declared intent.
        repository: ``org`` for your organization's library, ``fwd`` for the
            one Forward ships.
        source: The committed query text. This is the only place the source
            appears, and it is populated only when the query was fetched with
            ``with_source=True``; otherwise it is ``None``. Forward returns it
            as ``sourceCode`` on the wire.
    """

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
        """Flatten one entry from a query listing.

        Validated against the declared schema first, so a shape change is a
        validation error rather than a field quietly reading as None. The commit
        is then read tolerantly, because Forward nests it while integrations
        that normalize synthesize a flat key.
        """
        parsed = wire.RepositoryQuery.model_validate(dict(payload))
        nested = parsed.last_commit.id if parsed.last_commit else None
        return cls(
            query_id=str(parsed.query_id or payload.get("id") or ""),
            path=str(parsed.path or ""),
            commit_id=optional_str(nested or commit_id_of(payload)),
            intent=optional_str(parsed.intent),
            repository=str(parsed.repository or repository).lower(),
            source=optional_str(parsed.source_code),
        )


@dataclass(frozen=True, slots=True)
class DraftChange:
    """One staged, uncommitted change."""

    path: str
    action: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DraftChange:
        parsed = wire.DraftChange.model_validate(dict(payload))
        return cls(
            path=str(parsed.path or ""),
            action=optional_str(parsed.action or payload.get("type")),
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


def optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def commit_id_of(payload: Mapping[str, Any]) -> Any:
    """Read a query's commit id from whichever shape Forward sent.

    Forward nests it as ``lastCommit.id``. The flat ``lastCommitId`` is checked
    too, because integrations synthesize it when normalizing, so a payload that
    has already been through one is still understood.

    Getting this wrong loses the pin silently: a query resolved by path would
    run against whatever is at head, and a diff would stop being an assertion
    about a known version without anything failing.
    """
    nested = payload.get("lastCommit")
    if isinstance(nested, Mapping):
        found = nested.get("id")
        if found:
            return found
    return payload.get("lastCommitId") or payload.get("commitId")


def queries_from_payload(payload: Any, repository: str) -> list[RepositoryQuery]:
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


def message_of(error: Any) -> str | None:
    """The server's message for an error, from data rather than from display text.

    Prefers the parsed error body. Falls back to re-reading the raw body as
    JSON, which matters for endpoints Forward does not publish: their error
    envelopes are not guaranteed to carry the fields the parsed model requires,
    so it can be absent even though the server did send a message.

    Never falls back to ``str(error)``. Keying behaviour off an exception's
    display string is the pattern this SDK exists to remove from its consumers.
    """
    parsed = getattr(getattr(error, "error_info", None), "message", None)
    if parsed:
        return str(parsed)

    raw = getattr(error, "text", "") or ""
    if not raw.strip():
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if isinstance(body, Mapping):
        found = body.get("message")
        return str(found) if found else None
    return None


def paths_without_changes(message: str) -> set[str]:
    """Pull the offending paths out of Forward's rejection message."""
    match = re.search(re.escape(NO_CHANGES_PREFIX) + r"\s*(?P<paths>.+)", message, re.DOTALL)
    if not match:
        return set()
    return {
        part.strip().strip("'\"")
        for part in re.split(r"[,\n]", match.group("paths"))
        if part.strip()
    }
