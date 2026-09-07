"""Pure request builders.

Each Forward operation is described here as a function that turns arguments into
a :class:`RequestSpec`: a plain description of one HTTP request. Nothing in this
package performs I/O, which means the sync and async clients share every builder
verbatim, and the awkward parts (path quoting, fixed-query dispatch, repeated
parameters) are unit-testable without a server or an event loop.

Builders are registered with :func:`op`, which binds them to the generated
operation table. The method, path and fixed query always come from the table, so
a builder cannot drift from the spec, and ``tests/spec/test_coverage.py`` can
prove every operation is implemented exactly once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from forward_sdk._generated._defs import OpDef
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._http import build_path, encode_query

__all__ = ["REGISTRY", "RequestSpec", "op", "spec_for"]

JSON_ACCEPT = "application/json"

#: Prefer newline-delimited JSON for NQE results so rows can be streamed as they
#: arrive, but accept plain JSON from older deployments.
NDJSON_ACCEPT = "application/x-ndjson, application/jsonl;q=0.9, application/json;q=0.1"


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """One HTTP request, fully described and not yet sent.

    Attributes:
        operation: The operation this request implements.
        path: Path relative to the API root, already quoted.
        params: Query parameters, already flattened to repeated pairs.
        json: Body to send as JSON.
        content: Raw body bytes, when not JSON.
        files: Multipart parts, for snapshot upload.
        data: Multipart form fields.
        accept: Value for the ``Accept`` header.
        headers: Extra headers.
        timeout: Overrides the client's default timeout.
        idempotent: Whether the request may be re-sent after a response was
            already received. Retrying a create would duplicate work, so those
            are marked non-idempotent and retried only when the failure proves
            the server never saw them.
        stream: Whether the response body is consumed incrementally.
    """

    operation: OpDef
    path: str
    params: Sequence[tuple[str, str]] = ()
    json: Any = None
    content: bytes | None = None
    files: Sequence[tuple[str, Any]] | None = None
    data: Mapping[str, Any] | None = None
    accept: str = JSON_ACCEPT
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float | None = None
    idempotent: bool = True
    stream: bool = False

    @property
    def method(self) -> str:
        return self.operation.method.upper()

    @property
    def operation_id(self) -> str:
        return self.operation.operation_id

    def __str__(self) -> str:
        query = "&".join(f"{k}={v}" for k, v in self.params)
        return f"{self.method} {self.path}{'?' + query if query else ''}"


#: Every registered builder, keyed by ``operationId``.
REGISTRY: dict[str, Callable[..., RequestSpec]] = {}

F = TypeVar("F", bound=Callable[..., RequestSpec])

#: Methods that are safe to re-send after any failure. Everything else is
#: retried only when the request provably never reached the server.
IDEMPOTENT_METHODS = frozenset({"get", "head", "put", "delete", "options"})


def op(operation_id: str) -> Callable[[F], F]:
    """Register a builder as the implementation of ``operation_id``.

    Raises immediately if the id is unknown or already implemented, so a typo or
    an accidental duplicate fails at import rather than at request time.
    """

    def decorate(func: F) -> F:
        if operation_id not in OPERATIONS:
            raise KeyError(
                f"{operation_id!r} is not in the Forward API description. "
                "Add it to spec/unpublished.yaml if it is an unpublished endpoint."
            )
        if operation_id in REGISTRY:
            raise KeyError(f"{operation_id!r} already has a request builder")
        REGISTRY[operation_id] = func
        func.__forward_operation_id__ = operation_id  # type: ignore[attr-defined]
        return func

    return decorate


def spec_for(
    operation_id: str,
    *,
    path_params: Mapping[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
    json: Any = None,
    accept: str = JSON_ACCEPT,
    **kwargs: Any,
) -> RequestSpec:
    """Build a :class:`RequestSpec` for ``operation_id`` from the operation table.

    The operation's fixed query string is merged in ahead of caller parameters,
    so the dispatch that selects the operation cannot be overridden by accident.
    """
    operation = OPERATIONS[operation_id]
    kwargs.setdefault("idempotent", operation.method in IDEMPOTENT_METHODS)
    return RequestSpec(
        operation=operation,
        path=build_path(operation.path, path_params or {}),
        params=encode_query(query, operation.fixed_query),
        json=json,
        accept=accept,
        **kwargs,
    )
