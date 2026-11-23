"""Pure HTTP helpers.

Everything here is a plain function over plain values: no I/O, no client state.
That keeps the fiddly parts (URL joining, retry arithmetic, error messages)
directly testable, and lets the sync and async transports share them verbatim.
"""

from __future__ import annotations

import email.utils
import random
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote

from forward_sdk._generated._defs import OpDef
from forward_sdk._wire_shapes import ErrorBody, HttpResponse, QueryErrorBody
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardNqeQueryError,
    ForwardRateLimitError,
    status_error_class,
)

_API_PREFIX = "/api"

#: Status codes worth retrying. A 429 means "slow down", the 5xx entries are
#: transient infrastructure failures, and 408/425 are request-level retries the
#: server itself is asking for.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def normalize_base_url(base_url: str) -> str:
    """Return ``base_url`` with exactly one ``/api`` suffix.

    Users supply either the instance URL (``https://fwd.app``) or the API root
    (``https://fwd.app/api``); both must work.
    """
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("base_url must not be empty")
    if cleaned.endswith(_API_PREFIX):
        return cleaned
    return cleaned + _API_PREFIX


def _quote_segment(value: str) -> str:
    """Percent-encode one path segment, including ``/``.

    Forward puts user-chosen names in path segments -- device, alias, tag, VRF
    and file names -- and those routinely contain ``/``, ``:`` and spaces. Any
    unescaped separator would silently address a different resource.
    """
    return quote(str(value), safe="")


_PRE_ENCODED = re.compile(r"[A-Za-z0-9._~%!$&'()*+,;=:@-]+")


def _pre_encoded_segment(name: str, value: str) -> str:
    """Check a value Forward hands out already encoded for the path, and keep it as is.

    Encoding it again turns each ``%2F`` into ``%252F``, which Forward answers with
    400. It must still be one plain segment, so a separator, a query, or a dot
    segment is refused rather than passed on.
    """
    if not _PRE_ENCODED.fullmatch(value) or unquote(value) in (".", ".."):
        raise ValueError(f"path parameter {name!r} is not a single encoded path segment")
    return value


def build_path(template: str, values: Mapping[str, Any], pre_encoded: Collection[str] = ()) -> str:
    """Fill ``{placeholders}`` in an operation path, quoting each value.

    Names in ``pre_encoded`` take values Forward supplied already percent-encoded
    (a job's ``cancelLink``), which are not quoted a second time.
    """
    path = template
    for name, value in values.items():
        placeholder = "{" + name + "}"
        if placeholder in path:
            if value is None or value == "":
                raise ValueError(f"path parameter {name!r} must not be empty")
            if name in pre_encoded:
                path = path.replace(placeholder, _pre_encoded_segment(name, str(value)))
                continue
            if str(value) in (".", ".."):
                # Dots are unreserved, so quoting leaves them alone, and the HTTP
                # client then collapses the segment: a device named ".." would
                # address its parent resource instead.
                raise ValueError(f"path parameter {name!r} must not be {str(value)!r}")
            path = path.replace(placeholder, _quote_segment(value))
    if "{" in path:
        missing = path[path.index("{") + 1 : path.index("}")]
        raise ValueError(f"missing path parameter {missing!r} for {template!r}")
    return path


def encode_query(
    params: Mapping[str, Any] | None,
    fixed: Sequence[tuple[str, str | None]] = (),
) -> list[tuple[str, str]]:
    """Flatten query parameters, dropping ``None`` and expanding sequences.

    Forward repeats a parameter to pass several values (``?type=NQE&type=Predefined``)
    and dispatches some operations on a fixed query string, which is merged in
    first so a caller cannot accidentally override it.
    """
    for fixed_key, fixed_value in fixed:
        if fixed_value is not None and params and params.get(fixed_key) is not None:
            raise ValueError(
                f"{fixed_key!r} selects this operation and is fixed at {fixed_value!r}; "
                "it cannot be passed"
            )
    encoded: list[tuple[str, str]] = [(key, "" if value is None else value) for key, value in fixed]
    caller: list[tuple[str, str]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            caller.append((key, "true" if value else "false"))
        elif isinstance(value, (str, bytes)):
            caller.append((key, value.decode() if isinstance(value, bytes) else value))
        elif isinstance(value, Iterable):
            for item in value:
                if item is not None:
                    caller.append((key, _scalar(item)))
        else:
            caller.append((key, _scalar(value)))
    # A bare key in the dispatch string (``?action=duplicate&targetNetworkId``) only
    # makes the route match; the caller's value for the same key replaces it
    # instead of being sent alongside an empty one.
    supplied = {key for key, _ in caller}
    encoded = [(k, v) for k, v in encoded if not (k in supplied and (k, None) in set(fixed))]
    return encoded + caller


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(getattr(value, "value", value))


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Interpret a ``Retry-After`` header as seconds.

    Accepts both forms in RFC 9110: a delta in seconds, and an HTTP date. A date
    in the past, or an unparseable value, yields ``None`` so the caller falls
    back to its own backoff.
    """
    if not value:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return max(0.0, seconds)

    try:
        # Raises rather than returning None on 3.10+.
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (when - reference).total_seconds())


def backoff_delay(
    attempt: int,
    *,
    base: float,
    cap: float,
    retry_after: float | None = None,
    max_retry_after: float,
    jitter: random.Random | None = None,
) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based).

    Honours the server's ``Retry-After`` when it sent one, capped so a wildly
    large value cannot hang a caller. Otherwise applies full-jitter exponential
    backoff: jitter matters because SDK callers commonly fan out across a thread
    pool, and lockstep retries would re-stampede the server.
    """
    if retry_after is not None:
        return min(retry_after, max_retry_after)
    ceiling = min(cap, base * (2**attempt))
    rng = jitter or random
    return rng.uniform(0.0, ceiling)


#: How many query diagnostics to fold into an exception message before saying
#: how many more there are. The rest stay on ``.query_errors``.
_MAX_QUERY_ERRORS_IN_MESSAGE = 3


def _query_error_detail(error_info: ErrorBody | QueryErrorBody | None) -> str:
    """Summarize NQE's per-error diagnostics for the exception message.

    Forward's message for a failed query is the fixed string "Error encountered
    while executing the NQE query" for every failure, whatever went wrong. What
    actually went wrong is in ``errors``, each with a message and often a line
    and column. Leaving that out of the exception message makes every query
    failure read identically in a log, which is what a caller sees first.
    """
    errors = getattr(error_info, "errors", None) or []
    rendered: list[str] = []
    for item in errors[:_MAX_QUERY_ERRORS_IN_MESSAGE]:
        text = str(getattr(item, "message", "") or "").strip()
        if not text:
            continue
        start = getattr(getattr(item, "location", None), "start", None)
        line = getattr(start, "line", None)
        column = getattr(start, "column", None)
        where = f" at line {line}, column {column}" if line is not None else ""
        rendered.append(f"{text}{where}")
    if not rendered:
        return ""
    more = len(errors) - len(rendered)
    tail = f", and {more} more" if more > 0 else ""
    return f" -- {'; '.join(rendered)}{tail}"


def _error_message(response: HttpResponse, error_info: ErrorBody | None) -> str:
    """Build a message that leads with Forward's own explanation."""
    detail = getattr(error_info, "message", None)
    if not detail:
        detail = (response.text or "").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
    reason = getattr(error_info, "reason", None)
    suffix = f" ({reason})" if reason else ""
    request = response.request
    if not detail and response.status_code == 406:
        # Forward sends no body with this, so the message would read as if
        # nothing were known. What was refused is the Accept header.
        detail = (
            "Forward does not produce the representation requested "
            f"(Accept: {request.headers.get('accept', 'unset')})"
        )
    return (
        f"{request.method} {request.url.path} failed with HTTP "
        f"{response.status_code}: {detail or 'no response body'}{suffix}"
        f"{_query_error_detail(error_info)}"
    )


def raise_for_response(
    response: HttpResponse,
    *,
    operation: OpDef | None = None,
    attempts: int = 1,
    error_info: ErrorBody | None = None,
) -> None:
    """Raise the exception that represents ``response``, if it is an error.

    ``error_info`` is Forward's error body as the caller parsed it; this does
    not parse the response.
    """
    if response.status_code < 400:
        return

    message = _error_message(response, error_info)
    request = response.request

    kwargs: dict[str, Any] = {
        "status": response.status_code,
        "method": request.method,
        "url": str(request.url),
        "error_info": error_info,
        "text": response.text,
        "operation": operation,
        "attempts": attempts,
        "response": response,
    }

    if response.status_code == 429:
        raise ForwardRateLimitError(
            message,
            retry_after=parse_retry_after(response.headers.get("retry-after")),
            **kwargs,
        )

    error_class: type[ForwardAPIError] = status_error_class(response.status_code)
    if response.status_code == 400 and getattr(error_info, "errors", None) is not None:
        error_class = ForwardNqeQueryError
    raise error_class(message, **kwargs)
