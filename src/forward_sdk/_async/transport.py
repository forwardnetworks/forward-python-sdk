"""HTTP transport: the only place in the SDK that performs I/O.

This module is the source of truth for both clients. ``scripts/unasync.py``
mechanically derives the synchronous twin in ``forward_sdk._sync`` from it, so
retry behaviour, pacing and error mapping cannot diverge between them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from forward_sdk._async.throttle import AsyncRateLimiter, Throttle
from forward_sdk._http import RETRYABLE_STATUS, backoff_delay, parse_retry_after, raise_for_response
from forward_sdk._ops import RequestSpec
from forward_sdk.config import ClientConfig
from forward_sdk.errors import ForwardTransportError
from forward_sdk.telemetry import Counters, Hooks

logger = logging.getLogger("forward_sdk")

__all__ = ["AsyncTransport"]


class AsyncTransport:
    """Sends requests, with retries, pacing, and error mapping.

    One transport owns one ``httpx`` client for its lifetime, so connections are
    reused across calls. This matters: a sync run issues thousands of requests,
    and reconnecting for each one wastes a TLS handshake every time.
    """

    def __init__(
        self,
        config: ClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: Throttle | None = None,
        hooks: Hooks | None = None,
        counters: Counters | None = None,
    ) -> None:
        self.config = config
        self.counters = counters or Counters()
        self.hooks = hooks
        self._throttle = throttle or self._default_throttle(config)
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            auth=config.auth,
            verify=config.verify,
            timeout=config.timeout,
            transport=transport,
            trust_env=config.trust_env,
            proxy=config.proxy,
            follow_redirects=False,
            headers={"User-Agent": config.full_user_agent()},
        )

    @staticmethod
    def _default_throttle(config: ClientConfig) -> Throttle | None:
        if config.rate_limit_rpm:
            return AsyncRateLimiter(config.rate_limit_rpm)
        return None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _build(self, spec: RequestSpec) -> httpx.Request:
        headers = {"Accept": spec.accept, **dict(spec.headers)}
        return self._client.build_request(
            spec.method,
            spec.path,
            params=list(spec.params) or None,
            json=spec.json,
            content=spec.content,
            files=spec.files,
            data=spec.data,
            headers=headers,
            timeout=spec.timeout if spec.timeout is not None else self.config.timeout,
        )

    async def _pace(self) -> None:
        if self._throttle is None:
            return
        slept = await self._throttle.acquire()
        if slept:
            self.counters.increment("throttle_sleep_seconds", slept)
            self._notify("on_sleep", slept, "rate-limit")

    def _notify(self, hook: str, *args: Any) -> None:
        """Invoke a hook if the caller supplied one.

        Hooks are observability, so a broken hook must never fail the request
        that triggered it.
        """
        if self.hooks is None:
            return
        callback = getattr(self.hooks, hook, None)
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            logger.warning("forward_sdk hook %s raised; ignoring", hook, exc_info=True)

    async def _sleep_before_retry(
        self, spec: RequestSpec, attempt: int, delay: float, reason: str
    ) -> None:
        self.counters.increment("http_retries")
        self.counters.increment("retry_sleep_seconds", delay)
        self._notify("on_retry", spec.operation, attempt, delay, reason)
        logger.debug("retrying %s after %.2fs (attempt %d, %s)", spec, delay, attempt + 1, reason)
        await asyncio.sleep(delay)

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        policy = self.config.retries
        return backoff_delay(
            attempt,
            base=policy.backoff_base,
            cap=policy.backoff_cap,
            retry_after=retry_after,
            max_retry_after=policy.max_retry_after,
        )

    async def send(self, spec: RequestSpec) -> httpx.Response:
        """Send a request and return a successful response, or raise.

        Retries transient failures. A request whose body is not safe to repeat
        is retried only on a connection error, where the server demonstrably
        never received it.
        """
        policy = self.config.retries
        last_error: Exception | None = None

        for attempt in range(policy.max_attempts):
            final = attempt == policy.max_attempts - 1
            await self._pace()
            request = self._build(spec)
            self._notify("on_request", spec.operation, request)
            self.counters.mark_attempt(time.time())
            started = time.monotonic()

            try:
                response = await self._client.send(request)
            except httpx.TransportError as exc:
                self.counters.increment("transport_errors")
                if isinstance(exc, httpx.TimeoutException):
                    self.counters.increment("http_timeouts")
                last_error = exc
                if final or not _retry_on_transport_error(spec, exc):
                    raise ForwardTransportError(
                        f"{spec.method} {spec.path} failed: {exc}", attempts=attempt + 1
                    ) from exc
                await self._sleep_before_retry(
                    spec, attempt, self._retry_delay(attempt, None), type(exc).__name__
                )
                continue

            self._notify("on_response", spec.operation, response, time.monotonic() - started)
            self.counters.record_status(response.status_code)

            if response.status_code in RETRYABLE_STATUS and spec.idempotent and not final:
                if response.status_code == 429:
                    self.counters.increment("http_429")
                self.counters.increment("http_transient")
                await response.aread()
                delay = self._retry_delay(
                    attempt, parse_retry_after(response.headers.get("retry-after"))
                )
                await self._sleep_before_retry(spec, attempt, delay, f"HTTP {response.status_code}")
                continue

            if response.status_code >= 400:
                self.counters.increment("http_errors")
                if response.status_code == 429:
                    self.counters.increment("http_429")
                if response.status_code in RETRYABLE_STATUS:
                    self.counters.increment("http_transient")
                else:
                    self.counters.increment("http_rejected")
                await response.aread()
                raise_for_response(response, operation=spec.operation, attempts=attempt + 1)

            return response

        # Only reachable if the loop exhausted attempts without returning or
        # raising, which the retry branches above make impossible.
        raise ForwardTransportError(
            f"{spec.method} {spec.path} exhausted {policy.max_attempts} attempts",
            attempts=policy.max_attempts,
        ) from last_error

    @asynccontextmanager
    async def stream(self, spec: RequestSpec) -> AsyncIterator[httpx.Response]:
        """Open a streaming response.

        Retries cover connecting and receiving the response head. Once the body
        has started arriving a failure is surfaced, because rewinding a partly
        consumed stream would silently drop or duplicate rows; callers who need
        resumability should page instead.
        """
        policy = self.config.retries
        timeout = httpx.Timeout(
            connect=self.config.timeout.connect,
            read=self.config.stream_read_timeout,
            write=self.config.timeout.write,
            pool=self.config.timeout.pool,
        )

        for attempt in range(policy.max_attempts):
            final = attempt == policy.max_attempts - 1
            await self._pace()
            request = self._build(spec)
            request.extensions["timeout"] = timeout.as_dict()
            self._notify("on_request", spec.operation, request)
            self.counters.mark_attempt(time.time())

            response: httpx.Response | None = None
            try:
                response = await self._client.send(request, stream=True)
            except httpx.TransportError as exc:
                self.counters.increment("transport_errors")
                if final:
                    raise ForwardTransportError(
                        f"{spec.method} {spec.path} failed: {exc}", attempts=attempt + 1
                    ) from exc
                await self._sleep_before_retry(
                    spec, attempt, self._retry_delay(attempt, None), type(exc).__name__
                )
                continue

            if response.status_code in RETRYABLE_STATUS and not final:
                if response.status_code == 429:
                    self.counters.increment("http_429")
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                await response.aclose()
                await self._sleep_before_retry(
                    spec,
                    attempt,
                    self._retry_delay(attempt, retry_after),
                    f"HTTP {response.status_code}",
                )
                continue

            if response.status_code >= 400:
                self.counters.increment("http_errors")
                await response.aread()
                await response.aclose()
                raise_for_response(response, operation=spec.operation, attempts=attempt + 1)

            try:
                yield response
            finally:
                await response.aclose()
            return

        raise ForwardTransportError(  # pragma: no cover - unreachable, see send()
            f"{spec.method} {spec.path} exhausted {policy.max_attempts} attempts",
            attempts=policy.max_attempts,
        )


def _retry_on_transport_error(spec: RequestSpec, exc: httpx.TransportError) -> bool:
    """Whether a transport failure may be retried for this request.

    A connect error means the server never saw the request, so even a create is
    safe to repeat. Anything later (a read timeout, a dropped connection
    mid-flight) may have been processed, so only idempotent requests repeat.
    """
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return True
    return spec.idempotent
