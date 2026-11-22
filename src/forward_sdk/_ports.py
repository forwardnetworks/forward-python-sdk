"""What the SDK's adapters see of its core: the names they use, in one place.

The transport and the archive readers raise the SDK's errors, read its
configuration and report to its telemetry. They import those names from here,
not from the core modules, so the boundary between the core and the code that
talks to the outside world is one file. The objects are the same objects: an
``except ForwardTransportError`` written against :mod:`forward_sdk.errors`
catches what an adapter raises.
"""

from __future__ import annotations

from forward_sdk._http import RETRYABLE_STATUS, backoff_delay, parse_retry_after, raise_for_response
from forward_sdk.config import ClientConfig, tls_verify
from forward_sdk.errors import ForwardConfigurationError, ForwardTransportError
from forward_sdk.telemetry import Counters, Hooks

__all__ = [
    "RETRYABLE_STATUS",
    "ClientConfig",
    "Counters",
    "ForwardConfigurationError",
    "ForwardTransportError",
    "Hooks",
    "backoff_delay",
    "parse_retry_after",
    "raise_for_response",
    "tls_verify",
]
