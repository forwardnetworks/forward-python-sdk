# Generated from src/forward_sdk/_async/client.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""The Forward client."""

from __future__ import annotations

from typing import Any, Literal

import httpx

from forward_sdk._sync.services._generated import GeneratedServices
from forward_sdk._sync.services.ai import AiService
from forward_sdk._sync.services.device_tags import DeviceTagsService
from forward_sdk._sync.services.devices import DevicesService
from forward_sdk._sync.services.networks import NetworksService
from forward_sdk._sync.services.nqe import NqeService
from forward_sdk._sync.services.snapshots import SnapshotsService
from forward_sdk._sync.throttle import Throttle
from forward_sdk._sync.transport import Transport
from forward_sdk.config import ClientConfig, RetryPolicy, build_config, config_from_env
from forward_sdk.telemetry import Counters, CounterSnapshot, Hooks

__all__ = ["ForwardClient"]


class ForwardClient(GeneratedServices):
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
        >>> with ForwardClient.from_env() as client:
        ...     networks = client.networks.list()

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
        transport: httpx.BaseTransport | None = None,
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
        self._transport = Transport(
            self.config, transport=transport, throttle=throttle, hooks=hooks
        )
        self.networks = NetworksService(self._transport)
        self.snapshots = SnapshotsService(self._transport)
        self.devices = DevicesService(self._transport)
        self.device_tags = DeviceTagsService(self._transport)
        self.nqe = NqeService(self._transport)
        self.ai = AiService(self._transport)

        # The rest of the API, one attribute per group; see scripts/gen_services.py.
        self._attach_generated_services(self._transport)

    @classmethod
    def from_env(cls, **overrides: Any) -> ForwardClient:
        """Build a client from ``FORWARD_*`` environment variables.

        Reads ``FORWARD_URL``, ``FORWARD_USERNAME``, ``FORWARD_PASSWORD``,
        ``FORWARD_NETWORK_ID``, ``FORWARD_SNAPSHOT_ID``, ``FORWARD_VERIFY_TLS``,
        ``FORWARD_TIMEOUT``, ``FORWARD_RETRIES``, ``FORWARD_RATE_LIMIT_RPM`` and
        ``FORWARD_USER_AGENT``. Keyword arguments win over the environment.
        """
        settings = config_from_env(**overrides)
        base_url = settings.pop("base_url")
        return cls(base_url, **settings)

    def version(self) -> Any:
        """The Forward release this instance is running."""
        return self.networks.version()

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

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> ForwardClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<ForwardClient {self.config.base_url}>"
