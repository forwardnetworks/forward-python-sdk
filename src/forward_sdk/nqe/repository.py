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

    A plain frozen dataclass, not a pydantic model, so it has no
    ``model_dump``. Use :meth:`to_api` to serialize it with Forward's own field
    names. The type exists because Forward describes this entry in more than one
    shape, nesting the commit under ``lastCommit`` while integrations that
    normalize synthesize a flat ``lastCommitId``; reconciling that once here
    keeps every caller from doing it. Note that
    ``forward_sdk._generated.models`` also declares a ``RepositoryQuery``, the
    wire schema this is built from. That one is a pydantic model and is not what
    the library methods return.

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

    def to_api(self) -> dict[str, Any]:
        """Serialize with Forward's field names, matching the generated models.

        Named to match :meth:`ForwardModel.to_api`, so serializing an object the
        SDK returned does not depend on knowing which of the two kinds it is.
        Fields that are unset are omitted, as they are there.
        """
        data: dict[str, Any] = {
            "queryId": self.query_id,
            "path": self.path,
            "repository": self.repository,
        }
        if self.commit_id is not None:
            data["lastCommitId"] = self.commit_id
        if self.intent is not None:
            data["intent"] = self.intent
        if self.source is not None:
            data["sourceCode"] = self.source
        return data

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
    """One staged, uncommitted change.

    A frozen dataclass rather than a pydantic model, like
    :class:`RepositoryQuery`, so serialize it with :meth:`to_api`.
    """

    path: str
    action: str | None = None

    def to_api(self) -> dict[str, Any]:
        """Serialize with Forward's field names."""
        data: dict[str, Any] = {"path": self.path}
        if self.action is not None:
            data["action"] = self.action
        return data

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DraftChange:
        parsed = wire.DraftChange.model_validate(dict(payload))
        return cls(
            path=str(parsed.path or ""),
            action=optional_str(parsed.action or payload.get("type")),
        )


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One thing Forward's dry run found wrong, or worth a warning, in a query.

    Attributes:
        path: The library path of the query.
        severity: ``ERROR`` or ``WARNING``. Errors block a commit; warnings do
            not, which is how Forward's own commit dialog treats them.
        message: Forward's explanation, such as "Mismatched input 'this'.
            Reminder: record fields are comma-separated."
        location: Where in the source, as Forward reports it, when it does.
    """

    path: str
    severity: str
    message: str
    location: Mapping[str, Any] | None = None

    @property
    def is_error(self) -> bool:
        return self.severity.upper() == "ERROR"


def diagnostics_of(report: Mapping[str, Any]) -> tuple[Diagnostic, ...]:
    """Flatten a dry run's ``newErrors`` into diagnostics.

    ``newErrors`` is a map from every path the dry run examined to that path's
    diagnostics, and a path that compiles maps to an empty list. So a non-empty
    map means "the dry run looked at something", not "something is wrong", and
    the count that matters is of diagnostics with severity ``ERROR``. Reading
    the map's size as an error count refused every clean change.
    """
    found: list[Diagnostic] = []
    raw = report.get("newErrors") or {}
    if not isinstance(raw, Mapping):
        return ()
    for path, items in raw.items():
        for item in items or ():
            if not isinstance(item, Mapping):
                continue
            found.append(
                Diagnostic(
                    path=str(path),
                    severity=str(item.get("severity") or "ERROR"),
                    message=str(item.get("message") or ""),
                    location=item.get("location"),
                )
            )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class CommitReport:
    """The outcome of publishing queries.

    Attributes:
        warnings: Diagnostics of severity ``WARNING`` the dry run raised. They
            did not block the commit, matching Forward's dialog, but a caller
            publishing on someone's behalf may want to show them.
    """

    committed_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    commit_id: str | None = None
    dry_run: bool = False
    new_errors: tuple[Any, ...] = field(default_factory=tuple)
    warnings: tuple[Diagnostic, ...] = ()

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
    """Pull the offending paths out of Forward's rejection message.

    Forward writes the list as a sentence and terminates it with a full stop:
    ``User has no changes at the following paths: /a/q1, /a/q2.`` The stop is
    removed once, from the end of the list rather than from each path, so a path
    that genuinely ended in a dot would survive.

    Leaving it on was a real defect. The final path parsed as ``/a/q2.``, which
    matched nothing in the requested set, so the caller stripped every path but
    that one and retried a commit Forward refused again for the same reason.
    The second refusal escaped, and publishing an unchanged corpus raised
    instead of reporting that there was nothing to do. It applied to a
    single-path commit too, since that path is also the last one.
    """
    match = re.search(re.escape(NO_CHANGES_PREFIX) + r"\s*(?P<paths>.+)", message, re.DOTALL)
    if not match:
        return set()
    listed = match.group("paths").strip()
    if listed.endswith("."):
        listed = listed[:-1]
    return {part.strip().strip("'\"") for part in re.split(r"[,\n]", listed) if part.strip()}
