"""How a query is identified when running it.

Forward accepts three ways to name a query, and they are mutually exclusive:
inline source, a committed query's ID, or a path in the query library. A path
must be resolved to an ID before it can be run, and some operations (notably
snapshot diffs) accept only an ID. :class:`QueryRef` makes the choice explicit
and carries the resolution, so callers stop juggling three optional arguments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from forward_sdk.errors import ForwardConfigurationError

__all__ = ["LATEST_PROCESSED", "QueryRef", "SortKey", "sanitize_commit_id"]

#: Passed as a snapshot id to mean "whichever snapshot Forward considers
#: current". Forward expresses this by omitting the parameter entirely.
LATEST_PROCESSED = "latestProcessed"

#: A committed query ID, as shown in the query library.
QUERY_ID_PREFIX = "FQ_"

#: Length of a full commit hash. Forward rejects abbreviated ones.
COMMIT_ID_LENGTH = 40

HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

Repository = Literal["org", "fwd"]


@dataclass(frozen=True, slots=True)
class SortKey:
    """Sort instruction for a query's results."""

    column_name: str
    order: Literal["ASC", "DESC"] = "ASC"

    def to_api(self) -> dict[str, str]:
        return {"columnName": self.column_name, "order": self.order}


def sanitize_commit_id(commit_id: str | None) -> str | None:
    """Normalize a commit identifier, or reject one Forward cannot honour.

    ``head`` is dropped: Forward does not understand the symbolic name in a run
    request, and omitting the field means the same thing, so the caller gets
    what they asked for.

    An abbreviated hash is refused rather than dropped. Pinning a commit is an
    assertion about one specific version of a query; silently discarding the pin
    would answer a different question -- whatever is at head now -- and report
    success. That is exactly what pinning exists to prevent.

    Raises:
        ForwardConfigurationError: If the value looks like an abbreviated hash.
    """
    if not commit_id:
        return None
    value = commit_id.strip()
    if not value or value.lower() == "head":
        return None
    if len(value) < COMMIT_ID_LENGTH and all(c in HEX_DIGITS for c in value):
        raise ForwardConfigurationError(
            f"commit id {value!r} is abbreviated; Forward needs the full "
            f"{COMMIT_ID_LENGTH}-character hash. Dropping it would silently run "
            "against the latest version instead of the one you pinned."
        )
    return value


def normalize_query_path(path: str) -> str:
    """Normalize a library path to Forward's leading-slash form."""
    cleaned = path.strip()
    if not cleaned:
        raise ForwardConfigurationError("query path must not be empty")
    return cleaned if cleaned.startswith("/") else "/" + cleaned


@dataclass(frozen=True, slots=True)
class QueryRef:
    """Identifies a query to run, in exactly one of three ways.

    Build one with :meth:`inline`, :meth:`by_id` or :meth:`by_path` rather than
    calling the constructor directly.

    Attributes:
        text: Query source, to run without saving it.
        query_id: A committed query's ID.
        path: A path in the query library, resolved to an ID before running.
        repository: Which library a path refers to. ``org`` is your
            organization's; ``fwd`` is the one Forward ships.
        commit_id: Pins a specific version. Omit for the latest.
        parameters: Values for the query's declared parameters.
        sort_keys: Server-side ordering of results.
        resolved_query_id: Filled in once a path has been looked up.
        resolved_commit_id: The commit the resolution came from.
    """

    text: str | None = None
    query_id: str | None = None
    path: str | None = None
    repository: Repository = "org"
    commit_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    sort_keys: Sequence[SortKey] = ()
    resolved_query_id: str | None = None
    resolved_commit_id: str | None = None

    def __post_init__(self) -> None:
        provided = [name for name in ("text", "query_id", "path") if getattr(self, name)]
        if len(provided) != 1:
            raise ForwardConfigurationError(
                "a query reference needs exactly one of text, query_id or path; "
                f"got {provided or 'none'}"
            )
        if self.path:
            object.__setattr__(self, "path", normalize_query_path(self.path))

    @classmethod
    def inline(cls, text: str, **parameters: Any) -> QueryRef:
        """Run query source directly, without saving it to the library."""
        return cls(text=text, parameters=parameters)

    @classmethod
    def by_id(cls, query_id: str, *, commit_id: str | None = None, **parameters: Any) -> QueryRef:
        """Run a committed query by its ID (``FQ_...``)."""
        return cls(query_id=query_id, commit_id=commit_id, parameters=parameters)

    @classmethod
    def by_path(
        cls,
        path: str,
        *,
        repository: Repository = "org",
        commit_id: str | None = None,
        **parameters: Any,
    ) -> QueryRef:
        """Run a query by its path in the query library."""
        return cls(path=path, repository=repository, commit_id=commit_id, parameters=parameters)

    @property
    def mode(self) -> Literal["text", "query_id", "path"]:
        if self.text:
            return "text"
        if self.query_id:
            return "query_id"
        return "path"

    @property
    def effective_query_id(self) -> str | None:
        """The ID to send, preferring one obtained by resolving a path."""
        return self.query_id or self.resolved_query_id

    @property
    def effective_commit_id(self) -> str | None:
        return sanitize_commit_id(self.commit_id or self.resolved_commit_id)

    @property
    def is_runnable(self) -> bool:
        """Whether this can be sent as-is, without resolving a path first."""
        return bool(self.text or self.effective_query_id)

    def resolved(self, query_id: str, commit_id: str | None = None) -> QueryRef:
        """Return a copy carrying the ID a path lookup produced."""
        return replace(self, resolved_query_id=query_id, resolved_commit_id=commit_id)

    def with_parameters(self, **parameters: Any) -> QueryRef:
        return replace(self, parameters={**self.parameters, **parameters})

    def with_sort(self, *sort_keys: SortKey | str) -> QueryRef:
        keys = tuple(SortKey(k) if isinstance(k, str) else k for k in sort_keys)
        return replace(self, sort_keys=keys)

    def to_payload(self, *, include_sort: bool = True) -> dict[str, Any]:
        """Build the request body identifying this query.

        Raises:
            ForwardConfigurationError: If a library path has not been resolved
                to an ID yet. Call ``client.nqe.resolve()`` first.
        """
        payload: dict[str, Any] = {}
        if self.text:
            payload["query"] = self.text
        else:
            query_id = self.effective_query_id
            if not query_id:
                raise ForwardConfigurationError(
                    f"query path {self.path!r} has not been resolved to a query id; "
                    "resolve it before running"
                )
            payload["queryId"] = query_id
            commit_id = self.effective_commit_id
            if commit_id:
                payload["commitId"] = commit_id

        if self.parameters:
            payload["parameters"] = dict(self.parameters)
        if include_sort and self.sort_keys:
            payload["sortKeys"] = [key.to_api() for key in self.sort_keys]
        return payload

    def __str__(self) -> str:
        if self.text:
            snippet = " ".join(self.text.split())
            return f"inline query {snippet[:60]!r}"
        if self.query_id:
            return f"query {self.query_id}"
        return f"query at {self.path} ({self.repository})"
