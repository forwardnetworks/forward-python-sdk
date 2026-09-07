"""Networks and the API version."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._generated.models import Network
from forward_sdk._ops import core as ops
from forward_sdk.errors import ForwardNotFoundError

__all__ = ["AsyncNetworksService"]


class AsyncNetworksService(AsyncService):
    """Networks, and the workspaces forked from them."""

    async def list(self) -> list[Network]:
        """List every network you can see."""
        payload = await self._send_json(ops.list_networks())
        return [Network.model_validate(row) for row in payload or []]

    async def get(self, network_id: str) -> Network:
        """Find one network by id.

        Forward has no endpoint for a single network, so this filters the list.
        """
        for network in await self.list():
            if network.id == network_id:
                return network
        raise ForwardNotFoundError(f"no network with id {network_id!r}", status=404)

    async def create(self, name: str, *, note: str | None = None) -> Network:
        """Create a network.

        Forward's create endpoint takes only a name, so a note is applied as a
        follow-up update.
        """
        payload = await self._send_json(ops.create_network(name=name))
        network = Network.model_validate(payload or {})
        if note is not None and network.id:
            return await self.update(network.id, note=note)
        return network

    async def update(self, network_id: str, **changes: Any) -> Network:
        """Change a network's name, note or retention."""
        payload = await self._send_json(
            ops.update_network(network_id=self._network(network_id), changes=changes)
        )
        return Network.model_validate(payload or {})

    async def delete(self, network_id: str) -> None:
        """Delete a network and everything in it."""
        await self._send_json(ops.delete_network(network_id=self._network(network_id)))

    async def create_workspace(
        self,
        network_id: str | None = None,
        *,
        name: str | None = None,
        retention_days: int | None = None,
    ) -> Network:
        """Fork a network into a workspace for what-if changes."""
        payload = await self._send_json(
            ops.create_workspace(
                network_id=self._network(network_id),
                name=name,
                retention_days=retention_days,
            )
        )
        return Network.model_validate(payload or {})

    async def version(self) -> Mapping[str, Any]:
        """The Forward release this instance is running."""
        return dict(await self._send_json(ops.api_version()) or {})
