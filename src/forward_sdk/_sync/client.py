# Generated from src/forward_sdk/_async/client.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""The Forward client."""

from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches
from typing import Any, Literal

import httpx

from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._ops import REGISTRY
from forward_sdk._ops import rest as _rest  # noqa: F401  registers every builder
from forward_sdk._ops._generic import snake
from forward_sdk._sync.services._generated import GeneratedServices
from forward_sdk._sync.services.ai import AiService
from forward_sdk._sync.services.change_sets import ChangeSetsService
from forward_sdk._sync.services.client_software import ClientSoftwareService
from forward_sdk._sync.services.device_tags import DeviceTagsService
from forward_sdk._sync.services.devices import DevicesService
from forward_sdk._sync.services.diffs import SnapshotDiffsService
from forward_sdk._sync.services.events import UserEventsService
from forward_sdk._sync.services.networks import NetworksService
from forward_sdk._sync.services.nqe import NqeService
from forward_sdk._sync.services.snapshots import SnapshotsService
from forward_sdk._sync.services.tapi import TapiNetworkContainersService
from forward_sdk._sync.throttle import Throttle
from forward_sdk._sync.transport import Transport
from forward_sdk.access import Access
from forward_sdk.config import ClientConfig, RetryPolicy, build_config, config_from_env
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardNotFoundError,
    ForwardPermissionError,
)
from forward_sdk.rbac import RoleTable
from forward_sdk.telemetry import Counters, CounterSnapshot, Hooks

__all__ = ["ForwardClient"]


_UNREAD: Any = object()


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
        self._current_roles: Any = _UNREAD
        self._current_impersonator: str | None = None
        self.networks = NetworksService(self._transport)
        self.snapshots = SnapshotsService(self._transport)
        self.devices = DevicesService(self._transport)
        self.device_tags = DeviceTagsService(self._transport)
        self.nqe = NqeService(self._transport)
        self.ai = AiService(self._transport)
        self.snapshot_diffs = SnapshotDiffsService(self._transport)
        self.change_sets = ChangeSetsService(self._transport, self.snapshots)
        self.user_events = UserEventsService(self._transport)
        self.tapi_network_containers = TapiNetworkContainersService(self._transport)
        self.client_software = ClientSoftwareService(self._transport)

        # The rest of the API, one attribute per group; see scripts/gen_services.py.
        self._attach_generated_services(self._transport)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides: Any) -> ForwardClient:
        """Build a client from ``FORWARD_*`` environment variables.

        ``environ`` replaces ``os.environ`` as the source, for a caller that
        translates its own variable names first.

        Reads ``FORWARD_URL``, ``FORWARD_USERNAME``, ``FORWARD_PASSWORD``,
        ``FORWARD_NETWORK_ID``, ``FORWARD_SNAPSHOT_ID``, ``FORWARD_VERIFY_TLS``,
        ``FORWARD_TIMEOUT``, ``FORWARD_RETRIES``, ``FORWARD_RATE_LIMIT_RPM`` and
        ``FORWARD_USER_AGENT``. Keyword arguments win over the environment.
        """
        settings = config_from_env(None if environ is None else dict(environ), **overrides)
        base_url = settings.pop("base_url")
        return cls(base_url, **settings)

    def call(self, operation_id: str, /, **arguments: Any) -> Any:
        """Call any operation by its ``operationId``, with the generated methods' keywords.

        For callers that choose the operation at run time, such as a configuration
        tool driven by data. Arguments are the snake_case names the generated
        service methods use (``network_id``, ``snapshot_id``, ...), plus ``body``
        for a request body. ``network_id`` and ``snapshot_id`` fall back to the
        client's defaults when the operation takes them.

        Returns the decoded JSON, the response text when it is not JSON, or
        ``None`` for an empty response. Streaming operations are refused: use
        the service method that streams.

        Raises:
            ForwardConfigurationError: Unknown ``operation_id``, or a streaming one.
        """
        builder = REGISTRY.get(operation_id)
        operation = OPERATIONS.get(operation_id)
        if builder is None or operation is None:
            near = ", ".join(get_close_matches(operation_id, list(OPERATIONS), n=3))
            raise ForwardConfigurationError(
                f"unknown operation {operation_id!r}" + (f"; did you mean {near}?" if near else "")
            )
        if operation.stream:
            raise ForwardConfigurationError(
                f"{operation_id} streams its response; call the service method that streams it"
            )
        declared = {snake(p.name) for p in (*operation.path_params, *operation.query_params)}
        if "network_id" in declared and arguments.get("network_id") is None:
            arguments["network_id"] = self._transport.config.network_id
        if "snapshot_id" in declared and arguments.get("snapshot_id") is None:
            arguments["snapshot_id"] = self.snapshots._snapshot(None)
        arguments = {k: v for k, v in arguments.items() if v is not None}
        response = self._transport.send(builder(**arguments))
        if not response.content:
            return None
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.text

    def version(self) -> Any:
        """The Forward release this instance is running."""
        return self.networks.version()

    def access(
        self,
        network_id: str | None = None,
        *,
        refresh: bool = False,
        table: RoleTable | None = None,
    ) -> Access:
        """What the calling account may do, from the roles Forward reports for it.

        One call to ``GET /users/current``, then answers from Forward's own role
        table: ``(client.access("101")).may("NetworkOperation.
        TRIGGER_SNAPSHOT_COLLECTION")``. The roles are read once and kept on this
        client; ``refresh=True`` reads them again after a role change.

        That endpoint is unpublished, and some accounts are refused it (403) or find
        it absent (404). Neither raises: the result answers ``None`` to everything
        and says why, so a refusal to read the roles is never mistaken for a refusal
        of the operation. Bad credentials and server errors still raise. See
        :mod:`forward_sdk.access` for what ``True``, ``False`` and ``None`` mean, and
        :mod:`forward_sdk.rbac` for supplying the role table (``table=`` here, or the
        environment).
        """
        resolved = network_id or self._transport.config.network_id
        if refresh or self._current_roles is _UNREAD:
            try:
                current = self.user_accounts.get_current_user()
            except (ForwardPermissionError, ForwardNotFoundError) as error:
                return Access.unreadable_because(
                    f"HTTP {error.status} from /users/current", resolved, table
                )
            self._current_roles = current.roles
            extra = current.model_extra or {}
            self._current_impersonator = extra.get("impersonator")
        return Access.from_roles(
            self._current_roles, resolved, table, impersonator=self._current_impersonator
        )

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
