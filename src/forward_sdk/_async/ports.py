"""The transport as the services use it.

The services send request specs and read the client's configuration and
counters; they do not need the HTTP client behind them. Typing them against
this protocol, rather than the concrete transport, keeps them testable against
any object with these members.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

import httpx

from forward_sdk._ops import RequestSpec
from forward_sdk._ports import ClientConfig, Counters

__all__ = ["AsyncTransportPort"]


class AsyncTransportPort(Protocol):
    """What a service needs from a transport."""

    config: ClientConfig
    counters: Counters

    async def send(self, spec: RequestSpec) -> httpx.Response: ...

    def stream(self, spec: RequestSpec) -> AbstractAsyncContextManager[httpx.Response]: ...
