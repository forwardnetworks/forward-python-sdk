"""Transport behaviour: retries, pacing, error mapping, streaming.

Written against the async transport; ``scripts/unasync.py`` derives the sync
twin, so both clients are covered by one set of assertions.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from forward_sdk._async.throttle import AsyncRateLimiter
from forward_sdk._async.transport import AsyncTransport
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._ops import RequestSpec, spec_for
from forward_sdk.config import RetryPolicy, build_config
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardAuthError,
    ForwardConflictError,
    ForwardNotFoundError,
    ForwardNqeQueryError,
    ForwardPermissionError,
    ForwardRateLimitError,
    ForwardServerError,
    ForwardTransportError,
)
from forward_sdk.telemetry import Hooks
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio

NETWORKS = spec_for("getNetworks")


def make_transport(recorder: Recorder, **overrides: Any) -> AsyncTransport:
    config = build_config(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        **overrides,
    )
    return AsyncTransport(config, transport=recorder.transport)


async def test_sends_basic_auth_and_user_agent(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks", json_response([]))
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)

    request = recorder.requests[0]
    assert request.headers["authorization"].startswith("Basic ")
    assert request.headers["user-agent"].startswith("forward-sdk/")
    assert request.url.path == "/api/networks"


async def test_appends_api_prefix_only_once(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks", json_response([]))
    config = build_config("https://forward.test/api", rate_limit_rpm=None)
    async with AsyncTransport(config, transport=recorder.transport) as transport:
        await transport.send(NETWORKS)
    assert recorder.paths == ["/api/networks"]


async def test_retries_transient_status_then_succeeds(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    recorder.add(
        "GET",
        "/api/networks",
        error_response(503, "unavailable"),
        json_response([{"id": "1", "name": "n", "orgId": "o"}]),
    )
    async with make_transport(recorder) as transport:
        response = await transport.send(NETWORKS)

    assert response.status_code == 200
    assert recorder.count("GET", "/api/networks") == 2
    assert len(no_sleep) == 1
    assert transport.counters.snapshot().http_retries == 1


async def test_honours_retry_after_seconds(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add(
        "GET",
        "/api/networks",
        error_response(429, "slow down", headers={"Retry-After": "7"}),
        json_response([]),
    )
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)

    assert no_sleep == [7.0]
    assert transport.counters.snapshot().http_429 == 1


async def test_honours_retry_after_http_date(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add(
        "GET",
        "/api/networks",
        error_response(503, "later", headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}),
        json_response([]),
    )
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)

    # Capped by max_retry_after rather than the header's absurd distance.
    assert no_sleep == [RetryPolicy().max_retry_after]


async def test_gives_up_after_max_attempts(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add("GET", "/api/networks", error_response(503, "down"))
    async with make_transport(recorder, retries=2) as transport:
        with pytest.raises(ForwardServerError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.attempts == 3
    assert recorder.count("GET", "/api/networks") == 3


async def test_does_not_retry_authentication_failure(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    recorder.add("GET", "/api/networks", error_response(401, "bad credentials"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardAuthError):
            await transport.send(NETWORKS)

    assert recorder.count("GET", "/api/networks") == 1
    assert no_sleep == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ForwardAuthError),
        (403, ForwardPermissionError),
        (404, ForwardNotFoundError),
        (409, ForwardConflictError),
    ],
)
async def test_maps_status_to_exception(
    recorder: Recorder, status: int, expected: type[Exception]
) -> None:
    recorder.add("GET", "/api/networks", error_response(status, "no", reason="SOME_REASON"))
    async with make_transport(recorder) as transport:
        with pytest.raises(expected) as caught:
            await transport.send(NETWORKS)

    error = caught.value
    assert error.status == status  # type: ignore[attr-defined]
    assert error.reason == "SOME_REASON"  # type: ignore[attr-defined]
    assert "no" in str(error)


async def test_error_carries_forward_message_and_operation(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks", error_response(403, "not permitted"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(NETWORKS)

    error = caught.value
    assert error.error_info is not None
    assert error.error_info.message == "not permitted"
    assert error.operation is OPERATIONS["getNetworks"]


async def test_forward_message_and_body_survive_verbatim(recorder: Recorder) -> None:
    """Forward's own wording is load-bearing, so it is never reworded here.

    Forward exposes no endpoint for an account's licence tier, so a refusal
    arriving mid-sync is the only signal a tier denial exists. An integration
    tells that apart from an ordinary failure by matching Forward's text. If the
    SDK ever summarised, truncated or normalised the message, or dropped the raw
    body, that detection would go silent rather than fail, because the denial
    would look like any other 4xx.

    So: the message is carried character for character, and the undecoded body
    stays on the exception for anything the parsed model does not model.
    """
    message = "Feature 'Vulnerability Analysis' is not included in your licence tier (tier: BASE)."
    body = {"httpMethod": "GET", "apiUrl": "/api/networks", "message": message, "reason": "DENIED"}
    raw = json.dumps(body)
    recorder.add("GET", "/api/networks", httpx.Response(403, content=raw.encode()))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(NETWORKS)

    error = caught.value
    assert error.error_info is not None
    assert error.error_info.message == message
    assert error.reason == "DENIED"
    assert error.text == raw
    assert message in str(error)


async def test_error_body_parses_without_a_json_content_type(recorder: Recorder) -> None:
    """A proxy that rewrites the header must not erase Forward's explanation."""
    body = {"httpMethod": "GET", "apiUrl": "/api/networks", "message": "denied", "reason": "X"}
    recorder.add(
        "GET",
        "/api/networks",
        httpx.Response(
            403, content=json.dumps(body).encode(), headers={"content-type": "text/html"}
        ),
    )
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.error_info is not None
    assert caught.value.error_info.message == "denied"


async def test_a_denial_says_which_kind_it_is(recorder: Recorder) -> None:
    """403 carries no reason code, so the kind is read from Forward's wording."""
    recorder.add(
        "GET",
        "/api/networks",
        error_response(403, "Unlicensed operation: NetworkOperation.USE_NQE"),
    )
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(NETWORKS)

    error = caught.value
    assert error.reason is None, "Forward sets reason to null on a refusal"
    assert error.denial is not None
    assert error.denial.kind == "license"
    assert error.denial.detail == "NetworkOperation.USE_NQE"


async def test_an_unrecognised_denial_classifies_as_nothing(recorder: Recorder) -> None:
    """Not recognised is not the same as having no cause."""
    recorder.add("GET", "/api/networks", error_response(403, "nope"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.denial is None


async def test_gating_hint_surfaces_on_denial(recorder: Recorder) -> None:
    """A denial on a licence-gated group carries the documented hint."""
    spec = spec_for("getVulnerabilities", path_params={"networkId": "101"})
    recorder.add("GET", "/api/networks/101/vulnerabilities", error_response(403, "denied"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardPermissionError) as caught:
            await transport.send(spec)

    assert caught.value.gating == ("license",)


async def test_rate_limit_error_after_retries(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add("GET", "/api/networks", error_response(429, "too many"))
    async with make_transport(recorder, retries=1) as transport:
        with pytest.raises(ForwardRateLimitError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.status == 429


async def test_nqe_query_error_exposes_diagnostics(recorder: Recorder) -> None:
    body = {
        "httpMethod": "POST",
        "apiUrl": "/api/nqe",
        "message": "query failed",
        "completionType": "FINISHED",
        "errors": [{"message": "unknown field", "location": {"start": {"line": 2, "column": 5}}}],
    }
    recorder.add("POST", "/api/nqe", httpx.Response(400, json=body))
    spec = spec_for("runNqeQuery", json={"query": "bad"})
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardNqeQueryError) as caught:
            await transport.send(spec)

    error = caught.value
    assert error.completion_type == "FINISHED"
    assert len(error.query_errors) == 1
    assert error.query_errors[0].message == "unknown field"
    # Forward's own message is a fixed string for every query failure, so the
    # diagnostics have to reach the message or every failure reads alike in a
    # log. This is the first thing a caller sees.
    assert "unknown field" in str(error)
    assert "line 2, column 5" in str(error)


async def test_many_query_errors_are_summarised_not_dumped(recorder: Recorder) -> None:
    """The message stays readable; the full list stays on the exception."""
    errors = [
        {"message": f"problem {n}", "location": {"start": {"line": n, "column": 1}}}
        for n in range(1, 8)
    ]
    body = {
        "httpMethod": "POST",
        "apiUrl": "/api/nqe",
        "message": "Error encountered while executing the NQE query",
        "errors": errors,
    }
    recorder.add("POST", "/api/nqe", httpx.Response(400, json=body))
    spec = spec_for("runNqeQuery", json={"query": "bad"})
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardNqeQueryError) as caught:
            await transport.send(spec)

    message = str(caught.value)
    assert "problem 1" in message
    assert "and 4 more" in message
    assert "problem 7" not in message
    assert len(caught.value.query_errors) == 7


async def test_a_query_failure_without_diagnostics_still_reads_cleanly(
    recorder: Recorder,
) -> None:
    """No errors list means no dangling separator."""
    body = {
        "httpMethod": "POST",
        "apiUrl": "/api/nqe",
        "message": "Error encountered while executing the NQE query",
        "errors": [],
    }
    recorder.add("POST", "/api/nqe", httpx.Response(400, json=body))
    spec = spec_for("runNqeQuery", json={"query": "bad"})
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardNqeQueryError) as caught:
            await transport.send(spec)

    assert str(caught.value).endswith("Error encountered while executing the NQE query")


async def test_non_json_error_body_still_raises(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks", httpx.Response(502, text="<html>gateway</html>"))
    async with make_transport(recorder, retries=0) as transport:
        with pytest.raises(ForwardServerError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.error_info is None
    assert "gateway" in caught.value.text


async def test_retries_connect_error(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add("GET", "/api/networks", httpx.ConnectError("refused"), json_response([]))
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)

    assert recorder.count("GET", "/api/networks") == 2


async def test_transport_error_after_retries(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add("GET", "/api/networks", httpx.ConnectError("refused"))
    async with make_transport(recorder, retries=1) as transport:
        with pytest.raises(ForwardTransportError) as caught:
            await transport.send(NETWORKS)

    assert caught.value.attempts == 2


async def test_non_idempotent_request_not_retried_after_read_timeout(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    """A create that may have been processed must not be silently repeated."""
    spec = RequestSpec(
        operation=OPERATIONS["createNetwork"],
        path="/networks",
        json={"name": "n"},
        idempotent=False,
    )
    recorder.add("POST", "/api/networks", httpx.ReadTimeout("timed out"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardTransportError):
            await transport.send(spec)

    assert recorder.count("POST", "/api/networks") == 1


async def test_non_idempotent_request_retried_on_connect_error(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    """A connect failure proves the server never saw the request."""
    spec = RequestSpec(
        operation=OPERATIONS["createNetwork"],
        path="/networks",
        json={"name": "n"},
        idempotent=False,
    )
    recorder.add("POST", "/api/networks", httpx.ConnectError("refused"), json_response({"id": "1"}))
    async with make_transport(recorder) as transport:
        await transport.send(spec)

    assert recorder.count("POST", "/api/networks") == 2


async def test_rate_limiter_paces_requests(recorder: Recorder, no_sleep: list[float]) -> None:
    recorder.add("GET", "/api/networks", json_response([]))
    config = build_config("https://forward.test", rate_limit_rpm=600)
    async with AsyncTransport(config, transport=recorder.transport) as transport:
        await transport.send(NETWORKS)
        await transport.send(NETWORKS)

    # 600/minute is one every 0.1s; the first request is free.
    assert len(no_sleep) == 1
    assert no_sleep[0] == pytest.approx(0.1, abs=0.05)
    assert transport.counters.snapshot().throttle_sleep_seconds > 0


async def test_failures_are_counted_by_cause(recorder: Recorder, no_sleep: list[float]) -> None:
    """Each cause calls for a different response, so they are counted apart."""
    recorder.add("GET", "/api/networks", error_response(503, "later"), json_response([]))
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)
        counters = transport.counters.snapshot()

    assert counters.http_transient == 1
    assert counters.http_rejected == 0
    assert counters.http_timeouts == 0
    assert transport.counters.status_classes() == {"5xx": 1, "2xx": 1}


async def test_a_rejected_request_is_not_counted_as_transient(
    recorder: Recorder,
) -> None:
    recorder.add("GET", "/api/networks", error_response(400, "bad"))
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardAPIError):
            await transport.send(NETWORKS)
        counters = transport.counters.snapshot()

    assert counters.http_rejected == 1
    assert counters.http_transient == 0


async def test_timeouts_are_counted_apart_from_other_transport_errors(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    recorder.add("GET", "/api/networks", httpx.ReadTimeout("slow"))
    async with make_transport(recorder, retries=0) as transport:
        with pytest.raises(ForwardTransportError):
            await transport.send(NETWORKS)
        counters = transport.counters.snapshot()

    assert counters.http_timeouts == 1
    assert counters.transport_errors == 1


async def test_observed_request_rate_needs_a_window(
    recorder: Recorder, no_sleep: list[float]
) -> None:
    """A rate is uncomputable without the elapsed window, which is the point.

    One request stamps both ends of the window with the same instant, so the
    transport reports the timestamps and no rate. The arithmetic that turns
    them into a rate is exercised in tests/unit/test_telemetry.py, where the
    window can be chosen instead of measured.
    """
    recorder.add("GET", "/api/networks", json_response([]))
    async with make_transport(recorder) as transport:
        await transport.send(NETWORKS)
        counters = transport.counters.snapshot()

    assert counters.http_attempts == 1
    assert counters.first_attempt_at > 0
    assert counters.last_attempt_at >= counters.first_attempt_at
    assert counters.attempts_per_minute == 0.0


async def test_saas_default_rate_limit_applied() -> None:
    config = build_config("https://fwd.app", rate_limit_rpm="auto")
    assert config.rate_limit_rpm == 1800
    assert build_config("https://onprem.test", rate_limit_rpm="auto").rate_limit_rpm is None


async def test_query_parameters_repeat_and_merge_fixed_dispatch(recorder: Recorder) -> None:
    recorder.add("GET", "/api/snapshots/9/checks", json_response([]))
    spec = spec_for(
        "getChecks",
        path_params={"snapshotId": "9"},
        query={"type": ["NQE", "Predefined"], "status": None},
    )
    async with make_transport(recorder) as transport:
        await transport.send(spec)

    assert recorder.query_for()["type"] == ["NQE", "Predefined"]
    assert "status" not in recorder.query_for()


async def test_fixed_query_cannot_be_overridden(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/vulnerabilities", json_response({}))
    spec = spec_for("getVulnerabilities", path_params={"networkId": "101"}, query={"v": "1"})
    async with make_transport(recorder) as transport:
        await transport.send(spec)

    # The dispatch value that selects this operation comes first.
    assert recorder.query_for()["v"][0] == "2"


async def test_path_segments_are_quoted(recorder: Recorder) -> None:
    recorder.add("GET", "/api/networks/101/devices/nyc%2Ffw%3A01", json_response({}))
    spec = spec_for("getDevice", path_params={"networkId": "101", "deviceName": "nyc/fw:01"})
    async with make_transport(recorder) as transport:
        await transport.send(spec)

    assert (
        recorder.requests[0].url.raw_path.decode().startswith("/api/networks/101/devices/nyc%2Ffw")
    )


async def test_hooks_receive_lifecycle_events(recorder: Recorder, no_sleep: list[float]) -> None:
    events: list[str] = []

    class Recording(Hooks):
        def on_request(self, operation: Any, request: httpx.Request) -> None:
            events.append("request")

        def on_response(self, operation: Any, response: httpx.Response, elapsed: float) -> None:
            events.append(f"response:{response.status_code}")

        def on_retry(self, operation: Any, attempt: int, delay: float, reason: str) -> None:
            events.append(f"retry:{reason}")

    recorder.add("GET", "/api/networks", error_response(503, "x"), json_response([]))
    config = build_config("https://forward.test", rate_limit_rpm=None)
    async with AsyncTransport(config, transport=recorder.transport, hooks=Recording()) as t:
        await t.send(NETWORKS)

    assert events == ["request", "response:503", "retry:HTTP 503", "request", "response:200"]


async def test_broken_hook_does_not_fail_request(recorder: Recorder) -> None:
    class Exploding(Hooks):
        def on_request(self, operation: Any, request: httpx.Request) -> None:
            raise RuntimeError("boom")

    recorder.add("GET", "/api/networks", json_response([]))
    config = build_config("https://forward.test", rate_limit_rpm=None)
    async with AsyncTransport(config, transport=recorder.transport, hooks=Exploding()) as t:
        response = await t.send(NETWORKS)

    assert response.status_code == 200


async def test_stream_yields_body(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/nqe-executions/k/result",
        httpx.Response(200, content=b'{"a":1}\n{"a":2}\n'),
    )
    spec = spec_for(
        "getNqeExecutionResult",
        path_params={"networkId": "101", "executionKey": "k"},
        stream=True,
    )
    async with make_transport(recorder) as transport, transport.stream(spec) as response:
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == ['{"a":1}', '{"a":2}']


async def test_stream_retries_before_body_starts(recorder: Recorder, no_sleep: list[float]) -> None:
    """A stream that fails to connect is retried, unlike one that fails mid-body."""
    recorder.add(
        "GET",
        "/api/networks/101/nqe-executions/k/result",
        httpx.ConnectError("refused"),
        httpx.Response(200, content=b'{"a":1}\n'),
    )
    spec = spec_for(
        "getNqeExecutionResult",
        path_params={"networkId": "101", "executionKey": "k"},
        stream=True,
    )
    async with make_transport(recorder) as transport, transport.stream(spec) as response:
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == ['{"a":1}']
    assert recorder.count("GET", "/api/networks/101/nqe-executions/k/result") == 2


async def test_stream_maps_errors(recorder: Recorder) -> None:
    recorder.add(
        "GET",
        "/api/networks/101/nqe-executions/k/result",
        error_response(404, "no such execution"),
    )
    spec = spec_for(
        "getNqeExecutionResult",
        path_params={"networkId": "101", "executionKey": "k"},
        stream=True,
    )
    async with make_transport(recorder) as transport:
        with pytest.raises(ForwardNotFoundError):
            async with transport.stream(spec):
                pass


async def test_rate_limiter_first_acquire_is_free() -> None:
    limiter = AsyncRateLimiter(60)
    assert await limiter.acquire() == 0.0
