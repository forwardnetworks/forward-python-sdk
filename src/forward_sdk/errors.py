"""Exceptions raised by the SDK.

Every failed API call raises a subclass of :class:`ForwardAPIError` carrying the
parsed error body Forward returns on any 4xx or 5xx (an ``ErrorInfo``, read as
its ``message`` and ``reason``), so callers can read the server's own explanation
rather than guess from a status code.

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

import json
import re
from dataclasses import dataclass
from typing import Any

from forward_sdk._generated._defs import OpDef
from forward_sdk._wire_shapes import ErrorBody, HttpResponse, QueryDiagnostic, StagingReport

__all__ = [
    "Denial",
    "ForwardAPIError",
    "ForwardAuthError",
    "ForwardBadRequestError",
    "ForwardBgpKeyMatchedNothingError",
    "ForwardConfigurationError",
    "ForwardConflictError",
    "ForwardError",
    "ForwardExecutionError",
    "ForwardMcpError",
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
    """Background work finished without producing results.

    Raised for NQE executions and for snapshot and collection jobs. ``outcome``
    carries Forward's own term. For an NQE execution it is ``SYSTEM_ERROR`` (an
    unexpected internal failure), ``TIMED_OUT`` (it exceeded the execution's
    ``timeoutMinutes``) or ``USER_ERROR`` (the query itself failed); snapshot and
    collection jobs report their failure state instead, and ``outcome`` is
    ``None`` when there was nothing to report, such as no execution key.

    ``detail`` is Forward's ``error`` object as reported, or ``None``.
    ``attempts`` is how many times the SDK submitted the query, which is more
    than one only when ``query(..., outcome_retries=...)`` retried it.
    """

    def __init__(
        self,
        message: str,
        *,
        execution_key: str | None = None,
        status: str | None = None,
        outcome: str | None = None,
        detail: Any = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.execution_key = execution_key
        self.status = status
        self.outcome = outcome
        self.detail = detail
        self.attempts = attempts


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
    # UserPrincipal.verifyCan, reached from service code rather than the enforcer.
    ("rbac", re.compile(r"^No permission for network operation\s+(?P<detail>\S+)")),
    ("license_expired", re.compile(r"license has expired")),
    ("org_setting", re.compile(r"^(?P<detail>[A-Z0-9_]+) is (?:on|off) for your organization")),
    (
        "deployment_setting",
        re.compile(r"^(?P<detail>[A-Z0-9_]+) is (?:on|off) for your deployment"),
    ),
    ("authority", re.compile(r"^(?P<detail>.+?) authority required")),
    # QueryIdentityPolicy / QueryIdentityPolicyUtils: an NQE query (or any custom
    # query) the org's licence tier does not cover. "detail" is the query's path,
    # or its id (FQ_... or Q_...) when Forward did not resolve one. The apostrophe is
    # matched as "." only defensively: both sources write a straight one.
    (
        "license",
        re.compile(
            r"^(?:Query (?P<detail>.+?)|Custom Query) is not permitted for this "
            r"organization.s license tier"
        ),
    ),
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


#: Whole messages of refusals the error properties below classify. Anchored, so
#: a message that merely contains the words does not count.
_SERVICENOW_ASLEEP = re.compile(r"(?i)^ServiceNow instance is (hibernating|suspended)\.?$")
#: NewNetworkCheck's refusals: "'name' not allowed for Predefined checks", "'name'
#: required for parameterized NQE checks", "'tags' not allowed for NQE checks",
#: "'perfMonitoringEnabled' not allowed for Isolation checks" and so on; the type
#: is written as its JSON name.
_CHECK_FIELD_NOT_ALLOWED = re.compile(
    r"^'(name|note|tags|perfMonitoringEnabled)' (not allowed|required) for "
    r"((non-)?parameterized NQE|[A-Za-z]+) checks$"
)
_UNRECOGNIZED_DEVICES = re.compile(r"^Unrecognized devices cannot be moved: \[(.*)\]$")
_ORG_NOT_ACCESSIBLE = re.compile(r"^Org \S+ is not accessible to user \S+$")


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
        error_info: ErrorBody | None = None,
        text: str = "",
        operation: OpDef | None = None,
        attempts: int = 1,
        response: HttpResponse | None = None,
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
        # _message() also reads a body ErrorInfo could not parse, as the other
        # properties here do; matching the raw JSON text would miss it.
        return classify_denial(self._message())

    def _message(self) -> str:
        """Forward's message, from the parsed body, else the raw one, else the text."""
        parsed = getattr(self.error_info, "message", None)
        if parsed:
            return str(parsed)
        try:
            raw = json.loads(self.text)
        except ValueError:
            return self.text
        message = raw.get("message") if isinstance(raw, dict) else None
        return str(message) if message else self.text

    @property
    def endpoint_not_served(self) -> bool:
        """Whether Forward has no route for this request, rather than no such resource.

        Spring answers 404 ``No endpoint GET /api/backups.`` for a route this
        deployment does not map (backups on a hosted instance, say). It looks like
        a missing resource, and is not one: deleting through it did not delete
        anything, and retrying will not help.
        """
        return self.status == 404 and self._message().lstrip().startswith("No endpoint ")

    @property
    def action_not_supported(self) -> bool:
        """Whether this deployment does not map the ``?action=`` (or query condition) used.

        Spring answers 400 ``Parameter conditions "action=bulkAdd" not met for
        actual request parameters: ...`` when the route exists but not that
        variant, which is what an older build looks like for a newer action.
        A caller can then fall back to the older call (one
        ``add_bgp_advertisement`` per row instead of ``add_bgp_advertisements``)
        without matching Forward's text itself. Read from Spring's
        ``UnsatisfiedServletRequestParameterException``; not seen live.
        """
        message = self._message().lower()
        return self.status == 400 and "parameter conditions" in message and "not met" in message

    @property
    def servicenow_instance_asleep(self) -> bool:
        """Whether this is the 409 for a ServiceNow instance that is hibernating or suspended.

        Forward reads ServiceNow's wake-up page and refuses the call: nothing
        was created, and retrying once the instance is woken can work. The 409
        "Unknown Error" a woken instance answered live is NOT this (its cause
        is unconfirmed). Seen live on 26.10.6.
        """
        return self.status == 409 and bool(_SERVICENOW_ASLEEP.match(self._message().strip()))

    @property
    def unknown_network_property(self) -> bool:
        """Whether this is the 400 for a network property name Forward does not define.

        Forward defines far fewer network properties than org properties, so an
        org-only one such as ``location_connectivity_diffs`` written per network
        answers ``No enum constant ...NetworkProperty.X`` (seen live on 26.10.6).
        """
        message = self._message().lower()
        return self.status == 400 and "no enum constant" in message and "networkproperty" in message

    @property
    def check_field_not_allowed(self) -> bool:
        """Whether this is the 400 for a check field its type refuses (or requires).

        Forward's rules (``NewNetworkCheck``): no name on a Predefined or a
        non-parameterized NQE check, a name required on a parameterized NQE
        check, no note on an NQE check, tags only on path checks, and
        ``perfMonitoringEnabled`` only on an Existential one. The NQE wordings
        were seen live on 26.10.6; the rest are read from source.
        :func:`forward_sdk.checks.validate_new_check` applies the same rules
        before a request.
        """
        return self.status == 400 and bool(_CHECK_FIELD_NOT_ALLOWED.match(self._message().strip()))

    @property
    def unrecognized_devices(self) -> list[str] | None:
        """The devices of a 400 "Unrecognized devices cannot be moved: [r1, r2]", else ``None``.

        Forward refuses a whole atlas update when any device in it is not one
        the network knows as a collection source, even one present in every
        snapshot (a network fed only by uploads). Register such devices as
        classic devices with ``collect`` false in each ``NewClassicDevice``
        (``classic_devices.add_classic_devices``) and place them again (seen
        live on 26.10.6).
        """
        if self.status != 400:
            return None
        match = _UNRECOGNIZED_DEVICES.match(self._message().strip())
        if match is None:
            return None
        return [name.strip() for name in match.group(1).split(",") if name.strip()]

    @property
    def org_not_accessible(self) -> bool:
        """Whether this is the 403 "Org <id> is not accessible to user <id>".

        A support or cross-org key gets it for an organization that has not
        granted it access (seen live on 26.10.6 on ``/api/orgs/{id}/config``).
        """
        return self.status == 403 and bool(_ORG_NOT_ACCESSIBLE.match(self._message().strip()))

    @property
    def account_locked(self) -> bool:
        """Whether the 401 is because the account is locked, not because the credential is wrong.

        Forward says ``User account is locked: <name>`` for a user and
        ``Account is locked`` for an API token (source-verified; not seen live).
        """
        message = self._message()
        return self.status == 401 and (
            message.startswith("User account is locked") or message.strip() == "Account is locked"
        )

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
    def query_errors(self) -> tuple[QueryDiagnostic, ...]:
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


class ForwardBgpKeyMatchedNothingError(ForwardError):
    """A staged BGP advertisement or withdrawal matched nothing in the draft's diffs.

    Raised by ``change_set.verify_bgp_staging()`` when a withdrawal reads
    ``ABSENT`` (the base snapshot has no advertisement with that key) or a
    staged key has no diff row at all. Forward does not refuse such a stage; it
    ignores the key when the change set runs, so without this check a withdraw
    that matched nothing looks like one that worked. ``report`` holds the full
    read-back, one outcome per key.
    """

    def __init__(self, message: str, report: StagingReport) -> None:
        super().__init__(message)
        self.report = report


class ForwardMcpError(ForwardError):
    """Forward's MCP server answered a JSON-RPC error, or no answer to the request.

    A tool that ran and failed is not this: its result has ``is_error`` set.
    ``code`` and ``data`` are the JSON-RPC error's, when there was one.
    """

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
