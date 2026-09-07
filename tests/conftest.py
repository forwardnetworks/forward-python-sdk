"""Shared test fixtures.

Tests drive the SDK through ``httpx.MockTransport``, so they exercise the real
transport, retry and parsing code with no network and no server. The same
fixtures serve the async tests and the sync twins generated from them.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from forward_sdk.config import ClientConfig, build_config

Handler = Callable[[httpx.Request], httpx.Response]


def json_response(
    payload: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def error_response(
    status: int,
    message: str,
    *,
    method: str = "GET",
    url: str = "/api/test",
    reason: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> httpx.Response:
    """A response shaped like Forward's ErrorInfo body."""
    body: dict[str, Any] = {"httpMethod": method, "apiUrl": url, "message": message}
    if reason:
        body["reason"] = reason
    if extra:
        body.update(extra)
    return httpx.Response(status, json=body, headers=headers)


def ndjson_response(rows: Sequence[Any], status: int = 200) -> httpx.Response:
    body = "\n".join(json.dumps(row) for row in rows) + "\n"
    return httpx.Response(
        status, content=body.encode(), headers={"content-type": "application/x-ndjson"}
    )


@dataclass
class Recorder:
    """Records requests and replays scripted responses.

    A route may be given a list of responses to return in order, which is how
    retry and polling behaviour is tested: ``[429, 200]`` or
    ``[SUBMITTED, EXECUTING, COMPLETED]``.
    """

    routes: dict[tuple[str, str], list[httpx.Response | Exception]] = field(default_factory=dict)
    default: Handler | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def add(
        self,
        method: str,
        path: str,
        *responses: httpx.Response | Exception,
    ) -> Recorder:
        """Queue one or more responses for ``METHOD path``."""
        self.routes.setdefault((method.upper(), path), []).extend(responses)
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Routes may be written either decoded or percent-encoded; tests about
        # quoting need the raw form, everything else reads better decoded.
        raw_path = request.url.raw_path.decode().split("?", 1)[0]
        queued = self.routes.get((request.method, request.url.path)) or self.routes.get(
            (request.method, raw_path)
        )
        if queued:
            # The last queued response repeats, so a test need only script the
            # part of the sequence it cares about.
            item = queued.pop(0) if len(queued) > 1 else queued[0]
            if isinstance(item, Exception):
                raise item
            return item
        if self.default is not None:
            return self.default(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def query_for(self, index: int = -1) -> dict[str, list[str]]:
        url = self.requests[index].url
        return {key: url.params.get_list(key) for key in url.params}

    def body_for(self, index: int = -1) -> Any:
        content = self.requests[index].content
        return json.loads(content) if content else None

    def count(self, method: str, path: str) -> int:
        return sum(1 for r in self.requests if r.method == method.upper() and r.url.path == path)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def config() -> ClientConfig:
    """A client config with pacing off, so tests do not sleep."""
    return build_config(
        "https://forward.test",
        username="access-key",
        password="secret",
        rate_limit_rpm=None,
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make every sleep instantaneous and record how long it would have been.

    Retry and poll tests assert on the computed delays rather than enduring
    them, which keeps the suite fast and makes the backoff itself testable.
    """
    slept: list[float] = []

    async def fake_async_sleep(seconds: float, *args: Any, **kwargs: Any) -> None:
        slept.append(seconds)

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_async_sleep)
    monkeypatch.setattr(time, "sleep", fake_sleep)
    return slept


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sync", "async"])
def flavor(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def iter_lines(response: httpx.Response) -> Iterator[str]:
    yield from response.text.splitlines()
