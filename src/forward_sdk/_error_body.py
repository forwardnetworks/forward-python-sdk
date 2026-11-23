"""Parse Forward's error body into the generated model.

The core builds an exception from a response and the body parsed here; it reads
the body through the shapes in :mod:`forward_sdk._wire_shapes` and names no
generated model. Parsing needs those models and pydantic, so it lives with the
adapters' ports, and the transport calls it and passes the result on.
"""

from __future__ import annotations

from pydantic import ValidationError

from forward_sdk._generated.models import ErrorInfo, NqeErrorInfo
from forward_sdk._wire_shapes import HttpResponse
from forward_sdk.errors import ForwardResponseError

__all__ = ["parse_error_body"]


def parse_error_body(response: HttpResponse) -> ErrorInfo | NqeErrorInfo | None:
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
