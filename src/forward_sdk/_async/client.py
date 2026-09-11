"""The Forward client."""

from __future__ import annotations

from typing import Any, Literal

import httpx

from forward_sdk._async.services._generated import AsyncGeneratedServices
from forward_sdk._async.services.ai import AsyncAiService
from forward_sdk._async.services.change_sets import AsyncChangeSetsService
from forward_sdk._async.services.device_tags import AsyncDeviceTagsService
from forward_sdk._async.services.devices import AsyncDevicesService
from forward_sdk._async.services.diffs import AsyncSnapshotDiffsService
from forward_sdk._async.services.events import AsyncUserEventsService
from forward_sdk._async.services.networks import AsyncNetworksService
from forward_sdk._async.services.nqe import AsyncNqeService
from forward_sdk._async.services.snapshots import AsyncSnapshotsService
from forward_sdk._async.throttle import Throttle
from forward_sdk._async.transport import AsyncTransport
from forward_sdk.config import ClientConfig, RetryPolicy, build_config, config_from_env
from forward_sdk.telemetry import Counters, CounterSnapshot, Hooks

__all__ = ["AsyncForwardClient"]


class AsyncForwardClient(AsyncGeneratedServices):
    """Talks to a Forward Networks instance.

    Authentication is HTTP basic. Use an API token: its access key is the
    username and its secret is the password. A login name and password also
    work, but are subject to two-factor authentication and account lockout.

    One client holds one connection pool, so reuse it rather than creating one
    per call. It is bound to the event loop it was created on.

    Services hang off the client by group: ``client.networks``,
    ``client.snapshots``, ``client.devices``, ``client.device_tags`` and
    ``client.nqe`` are hand-written for the paths integrations use most; the
    remaining groups (``client.checks``, ``client.path_search``,
    ``client.aliases`` and so on) are generated from Forward's API description,
    and their method names are the operation ids in Python casing.

    Example:
        >>> async with AsyncForwardClient.from_env() as client:
        ...     networks = await client.networks.list()

    Args:
        base_url: The instance URL, with or without a trailing ``/api``.
        username: API token access key, or a login name.
        password: API token secret, or a password.
        verify: TLS verification. Disable only for a deployment using a
            self-signed certificate, and prefer passing a CA bundle path.
        timeout: A full ``httpx.Timeout``, or a read deadline in seconds.
        retries: A :class:`RetryPolicy`, or a retry count.
        rate_limit_rpm: Client-side pacing. ``"auto"`` paces requests against
            Forward's hosted service, where a server-side ceiling exists, and
            leaves self-hosted deployments alone.
        network_id: Default network for calls that omit one.
        snapshot_id: Default snapshot. ``None`` means each call uses the
            network's latest processed snapshot.
        user_agent: Identifies your application in the ``User-Agent`` header.
        transport: An ``httpx`` transport, mainly for tests.
        throttle: A custom pacer, for coordinating across processes.
        hooks: Callbacks around each request.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        verify: bool | str = True,
        timeout: httpx.Timeout | float | None = None,
        retries: RetryPolicy | int | None = None,
        rate_limit_rpm: int | Literal["auto"] | None = "auto",
        network_id: str | None = None,
        snapshot_id: str | None = None,
        user_agent: str | None = None,
        cache_ttl: float = 60.0,
        snapshot_cache_ttl: float | Literal["lifetime"] = 0.0,
        proxy: str | None = None,
        trust_env: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: Throttle | None = None,
        hooks: Hooks | None = None,
    ) -> None:
        self.config: ClientConfig = build_config(
            base_url,
            username=username,
            password=password,
            verify=verify,
            timeout=timeout,
            retries=retries,
            rate_limit_rpm=rate_limit_rpm,
            network_id=network_id,
            snapshot_id=snapshot_id,
            user_agent=user_agent,
            cache_ttl=cache_ttl,
            snapshot_cache_ttl=snapshot_cache_ttl,
            proxy=proxy,
            trust_env=trust_env,
        )
        self._transport = AsyncTransport(
            self.config, transport=transport, throttle=throttle, hooks=hooks
        )
        self.networks = AsyncNetworksService(self._transport)
        self.snapshots = AsyncSnapshotsService(self._transport)
        self.devices = AsyncDevicesService(self._transport)
        self.device_tags = AsyncDeviceTagsService(self._transport)
        self.nqe = AsyncNqeService(self._transport)
        self.ai = AsyncAiService(self._transport)
        self.snapshot_diffs = AsyncSnapshotDiffsService(self._transport)
        self.change_sets = AsyncChangeSetsService(self._transport, self.snapshots)
        self.user_events = AsyncUserEventsService(self._transport)

        # The rest of the API, one attribute per group; see scripts/gen_services.py.
        self._attach_generated_services(self._transport)

    @classmethod
    def from_env(cls, **overrides: Any) -> AsyncForwardClient:
        """Build a client from ``FORWARD_*`` environment variables.

        Reads ``FORWARD_URL``, ``FORWARD_USERNAME``, ``FORWARD_PASSWORD``,
        ``FORWARD_NETWORK_ID``, ``FORWARD_SNAPSHOT_ID``, ``FORWARD_VERIFY_TLS``,
        ``FORWARD_TIMEOUT``, ``FORWARD_RETRIES``, ``FORWARD_RATE_LIMIT_RPM`` and
        ``FORWARD_USER_AGENT``. Keyword arguments win over the environment.
        """
        settings = config_from_env(**overrides)
        base_url = settings.pop("base_url")
        return cls(base_url, **settings)

    async def version(self) -> Any:
        """The Forward release this instance is running."""
        return await self.networks.version()

    @property
    def counters(self) -> CounterSnapshot:
        """A reading of this client's request counters."""
        return self._transport.counters.snapshot()

    @property
    def counters_raw(self) -> Counters:
        """The live counters, for readings that are not a flat record.

        :meth:`Counters.status_classes` in particular, which is a mapping and
        so does not belong in the snapshot's scalars.
        """
        return self._transport.counters

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncForwardClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"<AsyncForwardClient {self.config.base_url}>"
