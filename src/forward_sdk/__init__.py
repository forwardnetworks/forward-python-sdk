"""Python SDK for the Forward Networks REST API.

A Forward Networks Field Integration: built and maintained by the field team,
not a supported Forward Networks product. See the README for what that means.
"""

from __future__ import annotations

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk._sync.client import ForwardClient
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
from forward_sdk.nqe import LATEST_PROCESSED, PageGuards, QueryRef

__all__ = [
    "LATEST_PROCESSED",
    "AsyncForwardClient",
    "ForwardAPIError",
    "ForwardAuthError",
    "ForwardBadRequestError",
    "ForwardClient",
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
    "PageGuards",
    "QueryRef",
    "__version__",
]
