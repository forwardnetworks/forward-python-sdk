"""Official Python SDK for the Forward Networks REST API."""

from __future__ import annotations

from forward_sdk._version import __version__
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardAuthError,
    ForwardBadRequestError,
    ForwardConfigurationError,
    ForwardConflictError,
    ForwardError,
    ForwardExecutionError,
    ForwardNotFoundError,
    ForwardNqeQueryError,
    ForwardPaginationError,
    ForwardPermissionError,
    ForwardRateLimitError,
    ForwardServerError,
    ForwardTimeoutError,
    ForwardTransportError,
)

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
    "__version__",
]
