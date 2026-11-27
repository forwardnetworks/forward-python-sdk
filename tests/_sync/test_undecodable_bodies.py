# Generated from tests/_async/test_undecodable_bodies.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Bodies that do not match their ``Content-Encoding``: what a caller can still do.

The error a caller gets must be usable like any other, and must stay a Forward
error however the response was read before the caller reached it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from forward_sdk._ops import client_software as ops
from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardNotFoundError, ForwardResponseError
from forward_sdk.telemetry import Hooks

pytestmark = pytest.mark.anyio


class _Corrupt(httpx.SyncByteStream):
    def __iter__(self) -> Any:
        yield b"this is not gzip"


def make_client(status: int, hooks: Hooks | None = None) -> ForwardClient:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-encoding": "gzip"}, stream=_Corrupt())

    return ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        transport=httpx.MockTransport(handle),
        hooks=hooks,
    )


def test_the_error_for_an_undecodable_error_body_carries_a_whole_response() -> None:
    with make_client(404) as client:
        with pytest.raises(ForwardNotFoundError) as raised:
            client.networks.list()

    response = raised.value.response
    assert isinstance(response, httpx.Response)
    assert response.status_code == 404
    assert response.elapsed >= timedelta(0)


def test_a_hook_that_reads_a_stream_first_does_not_change_the_callers_error() -> None:
    class ReadsInTheHook(Hooks):
        def __init__(self) -> None:
            self.responses: list[Any] = []

        def on_response(self, operation: Any, response: Any, elapsed: float) -> None:
            self.responses.append(response)

    hooks = ReadsInTheHook()
    with make_client(200, hooks) as client:
        with pytest.raises(ForwardResponseError):
            with client._transport.stream(
                ops.get_client_package(type="HEADLESS_LINUX")
            ) as response:
                # Stands for a hook that read the body: its own failure was ignored.
                with pytest.raises(ForwardResponseError):
                    hooks.responses[0].read()
                response.read()
