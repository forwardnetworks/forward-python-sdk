"""The shapes the core reads from a response and from Forward's error body.

The core builds an exception from an HTTP response and the error body Forward
sent with it. It reads only the members named here, through structural
protocols: the HTTP library's response and the generated wire models already
have them, so nothing in the core imports either. What the shapes say is what
the core uses and no more; a type that has these members is accepted.

The generated ``TextPosition`` declares ``character``, not ``column``, and
carries ``column`` only as an extra field when Forward sends one, so a
position's shape has ``line`` and the core reads ``column`` by name.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

__all__ = [
    "ErrorBody",
    "HttpResponse",
    "QueryDiagnostic",
    "QueryErrorBody",
    "QueryPosition",
    "QueryRegion",
]


class _Headers(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...


class _Url(Protocol):
    @property
    def path(self) -> str: ...


class _Request(Protocol):
    @property
    def method(self) -> str: ...

    @property
    def headers(self) -> _Headers: ...

    @property
    def url(self) -> _Url: ...


class HttpResponse(Protocol):
    """An HTTP response: status, body text, JSON, headers, and the request."""

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def headers(self) -> _Headers: ...

    @property
    def request(self) -> _Request: ...

    def json(self) -> Any: ...


class ErrorBody(Protocol):
    """Forward's error body: its explanation and a reason code, when it sent one."""

    @property
    def message(self) -> str: ...

    @property
    def reason(self) -> str | None: ...


class QueryPosition(Protocol):
    """A position in a query's source: its line.

    ``column`` is read by name, not typed: the generated position declares
    ``character`` and carries ``column`` only when Forward sends one.
    """

    @property
    def line(self) -> int | None: ...


class QueryRegion(Protocol):
    """A region of a query's source: where it starts."""

    @property
    def start(self) -> QueryPosition | None: ...


class QueryDiagnostic(Protocol):
    """One diagnostic of a failed NQE query: a message and where in the query it arose."""

    @property
    def message(self) -> str | None: ...

    @property
    def location(self) -> QueryRegion | None: ...


class QueryErrorBody(ErrorBody, Protocol):
    """An error body for a failed NQE query: the diagnostics and how the run ended."""

    @property
    def errors(self) -> Sequence[QueryDiagnostic] | None: ...

    @property
    def completion_type(self) -> object | None: ...
