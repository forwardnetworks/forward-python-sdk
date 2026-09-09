"""Exceptions raised by the SDK.

Every failed API call raises a subclass of :class:`ForwardAPIError` carrying the
parsed :class:`~forward_sdk._generated.models.ErrorInfo` body Forward returns on
any 4xx or 5xx, so callers can read the server's own explanation rather than
guess from a status code.

**On denials.** Forward reports a missing licence, a feature that is not part of
this deployment, and an RBAC denial with the same ordinary status codes, and
with ``reason`` set to ``null``, so no code separates them: 401 raises
:class:`ForwardAuthError`, 403 raises :class:`ForwardPermissionError` and 404
raises :class:`ForwardNotFoundError` whatever the underlying cause.

Its wording does separate them. :attr:`ForwardAPIError.denial` reads Forward's
own message and reports which kind of refusal it is, well enough to tell an
operator what to fix. It is prose rather than a contract, so branch on the
status code and treat the kind as the explanation. ``error.gating`` remains the
documented hint about what can gate an operation at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import httpx

    from forward_sdk._generated._defs import OpDef
    from forward_sdk._generated.models import ErrorInfo, NqeErrorInfo, NqeQueryError

__all__ = [
    "Denial",
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
    "ForwardResponseError",
    "ForwardServerError",
    "ForwardTimeoutError",
    "ForwardTransportError",
    "classify_denial",
    "status_error_class",
]


class ForwardError(Exception):
    """Base class for every error raised by this SDK."""


class ForwardConfigurationError(ForwardError):
    """The SDK was configured or called in a way that cannot work.

    Raised before any request is sent: a missing base URL, mutually exclusive
    query references, an NQE diff given an inline query.
    """


class ForwardResponseError(ForwardError):
    """Forward answered, but the body was not the shape the SDK expects.

    Raised where a response would otherwise fail with pydantic's
    ``ValidationError``, which is not a :class:`ForwardError` and so escapes
    every ``except ForwardError`` a caller has written. A sync would die with a
    validation traceback in a job log instead of a failure its own error
    handling could classify and report.

    The models are deliberately lenient in the other direction: unknown fields
    are kept and unknown enum values are tolerated, so a newer Forward does not
    break an older SDK. Required fields are the one strict place, and they are
    strict on purpose. They come from Forward's own generated description, so a
    missing one means the response is not the thing it claims to be, and a
    model with a hole in it would carry that silently into whatever the caller
    writes next.

    The originating ``ValidationError`` is kept as ``__cause__``, and the body
    that failed is on :attr:`payload`.

    Attributes:
        payload: What Forward actually sent, unparsed.
        model_name: The model the SDK tried to build from it.
    """

    def __init__(self, message: str, *, payload: Any = None, model_name: str = "") -> None:
        super().__init__(message)
        self.payload = payload
        self.model_name = model_name


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


#: How Forward words each kind of refusal, read from its own access enforcer
#: rather than guessed. ``reason`` is explicitly ``null`` on a 403, so the
#: message is the only signal there is.
#:
#: The licence-expiry pattern deliberately matches only the tail of Forward's
#: sentence. The full text contains a typographic apostrophe (U+2019), and
#: matching across it is how a check like this quietly stops working.
_DENIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("license", re.compile(r"^Unlicensed operation:\s*(?P<detail>\S+)")),
    ("rbac", re.compile(r"^Missing permission:\s*(?P<detail>\S+)")),
    ("license_expired", re.compile(r"license has expired")),
    ("org_setting", re.compile(r"^(?P<detail>[A-Z0-9_]+) is (?:on|off) for your organization")),
    (
        "deployment_setting",
        re.compile(r"^(?P<detail>[A-Z0-9_]+) is (?:on|off) for your deployment"),
    ),
    ("authority", re.compile(r"^(?P<detail>.+?) authority required")),
)


@dataclass(frozen=True, slots=True)
class Denial:
    """Why Forward refused, as far as its wording can tell you.

    Attributes:
        kind: One of ``rbac``, ``license``, ``license_expired``,
            ``org_setting``, ``deployment_setting`` or ``authority``.
        detail: The operation, setting or authority Forward named, when it
            named one.
    """

    kind: str
    detail: str | None = None


def classify_denial(message: str | None) -> Denial | None:
    """Work out what kind of refusal a message describes, or ``None``.

    Forward sends no machine-readable code for a refusal: its access enforcer
    builds an ``ErrorInfo`` with ``reason`` set to ``null``, so the message is
    the only thing that distinguishes a permission the account lacks from a
    feature its licence does not cover from a setting that is switched off.

    The wordings this reads are the enforcer's own format strings, so they are
    as stable as anything undocumented gets, but they are still prose and this
    is still a guess. Treat a result as a hint for an operator, never as a
    branch that changes what your code does. ``None`` means the wording was not
    recognised, which is not the same as the refusal having no cause.
    """
    if not message:
        return None
    text = message.strip()
    for kind, pattern in _DENIAL_PATTERNS:
        match = pattern.search(text)
        if match:
            detail = match.groupdict().get("detail")
            return Denial(kind=kind, detail=detail.strip() if detail else None)
    return None


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
        """Forward's machine-readable reason code, when it sent one.

        Endpoint-specific and often absent. Forward's ``ErrorInfo`` documents
        it as "a reason code whose possible values can vary by API endpoint",
        and a refusal always sets it to ``null``. For those, see
        :attr:`denial`.
        """
        return getattr(self.error_info, "reason", None)

    @property
    def denial(self) -> Denial | None:
        """What kind of refusal this is, read from Forward's wording.

        Populated for a refusal, ``None`` otherwise or when the wording was not
        recognised. Best-effort: see :func:`classify_denial` for why this reads
        prose, and why it is a hint rather than something to branch on.
        """
        detail = getattr(self.error_info, "message", None) or self.text
        return classify_denial(detail)

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

    The cause may be a permission the account lacks, a licence that does not
    cover the feature, or a setting switched off for the organisation or the
    deployment. No status code or reason code separates them: Forward sets
    ``reason`` to ``null`` on every refusal.

    Its wording does separate them, and :attr:`denial` reads it. That is prose
    rather than a contract, so use it to tell an operator what to fix, not to
    decide what your code does next.
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
