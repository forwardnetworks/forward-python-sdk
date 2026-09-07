"""Exceptions raised by the SDK.

Every failed API call raises a subclass of :class:`ForwardAPIError` carrying the
parsed :class:`~forward_sdk._generated.models.ErrorInfo` body Forward returns on
any 4xx or 5xx, so callers can read the server's own explanation rather than
guess from a status code.

**On denials.** Forward reports a missing licence, a feature that is not part of
this deployment, and an RBAC denial with the same ordinary status codes. The SDK
therefore does not claim to distinguish them: 401 raises
:class:`ForwardAuthError`, 403 raises :class:`ForwardPermissionError` and 404
raises :class:`ForwardNotFoundError` whatever the underlying cause. Read
``error.error_info.message`` for the server's reason, and ``error.gating`` for a
documented hint about what can gate that operation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import httpx

    from forward_sdk._generated._defs import OpDef
    from forward_sdk._generated.models import ErrorInfo, NqeErrorInfo, NqeQueryError

__all__ = [
    "ForwardAPIError",
    "ForwardAuthError",
    "ForwardBadRequestError",
    "ForwardConfigurationError",
    "ForwardConflictError",
    "ForwardError",
    "ForwardExecutionError",
    "ForwardNotFoundError",
    "ForwardNqeQueryError",
    "ForwardPaginationError",
    "ForwardPermissionError",
    "ForwardRateLimitError",
    "ForwardServerError",
    "ForwardTimeoutError",
    "ForwardTransportError",
    "status_error_class",
]


class ForwardError(Exception):
    """Base class for every error raised by this SDK."""


class ForwardConfigurationError(ForwardError):
    """The SDK was configured or called in a way that cannot work.

    Raised before any request is sent: a missing base URL, mutually exclusive
    query references, an NQE diff given an inline query.
    """


class ForwardTransportError(ForwardError):
    """The request never produced an HTTP response.

    Covers connection failures, TLS errors and timeouts, after retries are
    exhausted. The originating ``httpx`` exception is the ``__cause__``.
    """

    def __init__(self, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class ForwardTimeoutError(ForwardError):
    """A client-side deadline expired while waiting on Forward.

    Raised by polling helpers such as ``execution.wait()`` and
    ``snapshots.wait_until_processed()``. The server-side work usually
    continues; the SDK simply stopped waiting.
    """


class ForwardPaginationError(ForwardError):
    """Paging through a result set could not complete safely.

    Raised when the server stops before the total it promised, when a page
    repeats without advancing, or when a configured page or row ceiling is
    reached. Carries how much was collected before stopping.
    """

    def __init__(self, message: str, *, rows: int = 0, pages: int = 0) -> None:
        super().__init__(message)
        self.rows = rows
        self.pages = pages


class ForwardExecutionError(ForwardError):
    """An NQE execution finished without producing results.

    The query ran but ended with a non-OK outcome: it timed out on the server,
    was canceled, or failed. ``outcome`` carries Forward's own term.
    """

    def __init__(
        self,
        message: str,
        *,
        execution_key: str | None = None,
        status: str | None = None,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.execution_key = execution_key
        self.status = status
        self.outcome = outcome


class ForwardAPIError(ForwardError):
    """Forward returned an error response.

    Attributes:
        status: The HTTP status code.
        method: The HTTP method that was sent.
        url: The URL that was requested.
        error_info: Forward's parsed error body, when it sent one.
        text: The raw response body, always available.
        operation: The operation definition, when the call came from a service
            method rather than a raw request.
        attempts: How many times the request was sent, including retries.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        method: str = "",
        url: str = "",
        error_info: ErrorInfo | NqeErrorInfo | None = None,
        text: str = "",
        operation: OpDef | None = None,
        attempts: int = 1,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.method = method
        self.url = url
        self.error_info = error_info
        self.text = text
        self.operation = operation
        self.attempts = attempts
        self.response = response

    @property
    def reason(self) -> str | None:
        """Forward's machine-readable reason code, when it sent one."""
        return getattr(self.error_info, "reason", None)

    @property
    def gating(self) -> tuple[str, ...]:
        """What is documented to gate this operation.

        A hint for interpreting a denial, not a detection: an empty tuple does
        not mean the call failed for some other reason. See ``docs/gating.md``.
        """
        return self.operation.gating if self.operation else ()


class ForwardBadRequestError(ForwardAPIError):
    """400: Forward rejected the request as malformed or invalid."""


class ForwardNqeQueryError(ForwardBadRequestError):
    """An NQE query failed to compile or run.

    ``query_errors`` carries Forward's diagnostics, each with the position in
    the query source that caused it.
    """

    @property
    def query_errors(self) -> tuple[NqeQueryError, ...]:
        return tuple(getattr(self.error_info, "errors", None) or ())

    @property
    def completion_type(self) -> str | None:
        value = getattr(self.error_info, "completion_type", None)
        return str(value) if value is not None else None


class ForwardAuthError(ForwardAPIError):
    """401: Forward did not accept the credentials.

    Also raised when credentials are valid but the account cannot be used, for
    example when its organization has no active licence.
    """


class ForwardPermissionError(ForwardAPIError):
    """403: Forward refused the request.

    The cause may be RBAC, a licence that does not cover the feature, or a
    feature absent from this deployment. These are not distinguishable from the
    response; see the module docstring.
    """


class ForwardNotFoundError(ForwardAPIError):
    """404: no such object, or no such feature on this deployment.

    Forward also returns 404 where a caller may not see that an object exists,
    so this does not prove absence.
    """


class ForwardConflictError(ForwardAPIError):
    """409: the request conflicts with the current state.

    Common causes are querying a snapshot that is not processed yet, and NQE
    repository writes that no longer match the drafts on the server.
    """


class ForwardRateLimitError(ForwardAPIError):
    """429: Forward is rate limiting this client.

    Raised only after the configured retries are exhausted. ``retry_after``
    carries the server's requested delay in seconds, when it sent one.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ForwardServerError(ForwardAPIError):
    """5xx: Forward failed to handle the request."""


_STATUS_ERRORS: dict[int, type[ForwardAPIError]] = {
    400: ForwardBadRequestError,
    401: ForwardAuthError,
    403: ForwardPermissionError,
    404: ForwardNotFoundError,
    409: ForwardConflictError,
    429: ForwardRateLimitError,
}


def status_error_class(status: int) -> type[ForwardAPIError]:
    """Return the exception class that represents ``status``."""
    if status in _STATUS_ERRORS:
        return _STATUS_ERRORS[status]
    if status >= 500:
        return ForwardServerError
    return ForwardAPIError
