"""Pure HTTP helpers.

Everything here is a plain function over plain values: no I/O, no client state.
That keeps the fiddly parts (URL joining, retry arithmetic, error-body parsing)
directly testable, and lets the sync and async transports share them verbatim.
"""

from __future__ import annotations

import email.utils
import random
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from forward_sdk._generated._defs import OpDef
from forward_sdk._generated.models import ErrorInfo, NqeErrorInfo
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardNqeQueryError,
    ForwardRateLimitError,
    ForwardResponseError,
    status_error_class,
)

API_PREFIX = "/api"

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
    if cleaned.endswith(API_PREFIX):
        return cleaned
    return cleaned + API_PREFIX


def quote_segment(value: str) -> str:
    """Percent-encode one path segment, including ``/``.

    Forward puts user-chosen names in path segments -- device, alias, tag, VRF
    and file names -- and those routinely contain ``/``, ``:`` and spaces. Any
    unescaped separator would silently address a different resource.
    """
    return quote(str(value), safe="")


def build_path(template: str, values: Mapping[str, Any]) -> str:
    """Fill ``{placeholders}`` in an operation path, quoting each value."""
    path = template
    for name, value in values.items():
        placeholder = "{" + name + "}"
        if placeholder in path:
            if value is None or value == "":
                raise ValueError(f"path parameter {name!r} must not be empty")
            path = path.replace(placeholder, quote_segment(value))
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
    encoded: list[tuple[str, str]] = [(key, "" if value is None else value) for key, value in fixed]
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded.append((key, "true" if value else "false"))
        elif isinstance(value, (str, bytes)):
            encoded.append((key, value.decode() if isinstance(value, bytes) else value))
        elif isinstance(value, Iterable):
            for item in value:
                if item is not None:
                    encoded.append((key, _scalar(item)))
        else:
            encoded.append((key, _scalar(value)))
    return encoded


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


def parse_error_body(response: httpx.Response) -> Any:
    """Parse Forward's error body into a model, or ``None`` if it is not one.

    Forward maps errors centrally and returns ``ErrorInfo`` on any 4xx or 5xx,
    even where the spec documents no such response, so this is attempted for
    every failure. NQE extends the shape with query diagnostics.

    The body is parsed on its own merits rather than on the content type it
    arrives with. A proxy that rewrites or drops the header would otherwise turn
    a described denial into a bare status, and silently: consumers tell a licence
    or permission denial from an ordinary failure by reading Forward's message,
    because no endpoint reports an account's entitlements. Anything that is not
    a JSON object still parses to ``None``, so an HTML gateway page is unchanged.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    model = NqeErrorInfo if ("errors" in payload or "completionType" in payload) else ErrorInfo
    try:
        return model.model_validate(payload)
    except (ValidationError, ForwardResponseError):
        # A malformed error body must never mask the HTTP failure it describes.
        # Both are caught because ForwardModel turns the first into the second;
        # naming only one would make an unreadable error body raise while the
        # SDK was building the exception for the failure it belongs to.
        return None


def error_message(response: httpx.Response, error_info: Any) -> str:
    """Build a message that leads with Forward's own explanation."""
    detail = getattr(error_info, "message", None)
    if not detail:
        detail = (response.text or "").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
    reason = getattr(error_info, "reason", None)
    suffix = f" ({reason})" if reason else ""
    request = response.request
    return (
        f"{request.method} {request.url.path} failed with HTTP "
        f"{response.status_code}: {detail or 'no response body'}{suffix}"
    )


def raise_for_response(
    response: httpx.Response,
    *,
    operation: OpDef | None = None,
    attempts: int = 1,
) -> None:
    """Raise the exception that represents ``response``, if it is an error."""
    if response.status_code < 400:
        return

    error_info = parse_error_body(response)
    message = error_message(response, error_info)
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
