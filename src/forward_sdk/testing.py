"""Helpers for testing code that uses the SDK without a Forward instance.

A client built with ``transport=recorder.transport`` runs the real transport,
retry and parsing code against scripted responses, so a test fails when the SDK
changes how it talks to Forward rather than passing against a stale mock.

```python
recorder = ForwardRecorder()
recorder.add("GET", "/api/networks/101/snapshots", httpx.Response(200, json={"snapshots": [
    snapshot_payload("s1", created_at="2026-01-02T00:00:00Z"),
]}))
client = ForwardClient("https://forward.test", username="k", password="s",
                       network_id="101", rate_limit_rpm=None, transport=recorder.transport)
```
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = ["ForwardRecorder", "snapshot_payload"]

Handler = Callable[[httpx.Request], httpx.Response]


def snapshot_payload(
    snapshot_id: str,
    *,
    state: str = "PROCESSED",
    created_at: str = "2026-01-01T00:00:00Z",
    processed_at: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A snapshot as Forward lists it.

    ``createdAt`` is always present because ``latest_processed`` orders by it;
    a hand-written payload without one picks the wrong snapshot. Extra keyword
    arguments are added verbatim, so use Forward's camelCase names
    (``parentSnapshotId``, ``isDraft``).
    """
    payload: dict[str, Any] = {
        "id": snapshot_id,
        "state": state,
        "createdAt": created_at,
        "processedAt": processed_at or created_at,
    }
    payload.update(extra)
    return payload


@dataclass
class ForwardRecorder:
    """Records requests and replays scripted responses.

    A route may be given several responses, returned in order with the last one
    repeating, which is how retry and polling are tested: ``[429, 200]`` or
    ``[SUBMITTED, EXECUTING, COMPLETED]``. An exception queued as a response is
    raised, simulating a transport failure.
    """

    routes: dict[tuple[str, str], list[httpx.Response | Exception]] = field(default_factory=dict)
    default: Handler | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def add(
        self, method: str, path: str, *responses: httpx.Response | Exception
    ) -> ForwardRecorder:
        """Queue responses for ``METHOD path`` (the path without its query string)."""
        self.routes.setdefault((method.upper(), path), []).extend(responses)
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw_path = request.url.raw_path.decode().split("?", 1)[0]
        queued = self.routes.get((request.method, request.url.path)) or self.routes.get(
            (request.method, raw_path)
        )
        if queued:
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
