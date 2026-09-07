"""Shared plumbing for service objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forward_sdk._ops import RequestSpec
from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.nqe.query_ref import LATEST_PROCESSED

if TYPE_CHECKING:  # pragma: no cover
    from forward_sdk._async.transport import AsyncTransport


class AsyncService:
    """Base for the grouped call surfaces hanging off a client."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    @property
    def _config(self) -> Any:
        return self._transport.config

    def _network(self, network_id: str | None) -> str:
        """Resolve a network id, falling back to the client's default."""
        resolved = network_id or self._config.network_id
        if not resolved:
            raise ForwardConfigurationError(
                "no network id: pass network_id, or set one on the client "
                "(ForwardClient(..., network_id=...) or FORWARD_NETWORK_ID)"
            )
        return str(resolved)

    def _snapshot(self, snapshot_id: str | None) -> str | None:
        """Resolve a snapshot id.

        ``None`` is meaningful: Forward interprets an omitted snapshot as the
        network's latest processed one, so it is passed through rather than
        resolved eagerly.
        """
        resolved = snapshot_id if snapshot_id is not None else self._config.snapshot_id
        if resolved in (None, "", LATEST_PROCESSED):
            return None
        return str(resolved)

    async def _send_json(self, spec: RequestSpec) -> Any:
        response = await self._transport.send(spec)
        if not response.content:
            return None
        return response.json()
