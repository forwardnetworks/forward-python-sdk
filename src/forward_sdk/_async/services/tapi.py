"""T-API optical network containers.

Unpublished. Forward models an optical network from T-API (Transport API)
topology documents: a container is a named synthetic device whose model is the
merge of the JSON documents uploaded for it. Like every synthetic device, a
change applies to the network's next snapshot unless it is backdated.
"""

from __future__ import annotations

from collections.abc import Sequence

from forward_sdk._async.services._base import AsyncService
from forward_sdk._generated.models import TapiNetworkContainer
from forward_sdk._ops import tapi as ops
from forward_sdk._ops.tapi import TapiFile

__all__ = ["AsyncTapiNetworkContainersService", "TapiFile"]


class AsyncTapiNetworkContainersService(AsyncService):
    """Create, read and delete T-API optical network containers."""

    async def list(self, network_id: str | None = None) -> list[TapiNetworkContainer]:
        """Every container, each with its parsed model."""
        payload = await self._send_json(ops.list_containers(network_id=self._network(network_id)))
        items = (payload or {}).get("containers") or []
        return [TapiNetworkContainer.model_validate(item) for item in items]

    async def get(self, name: str, *, network_id: str | None = None) -> TapiNetworkContainer:
        """One container by name; 404 when there is none."""
        payload = await self._send_json(
            ops.get_container(network_id=self._network(network_id), container_name=name)
        )
        return TapiNetworkContainer.model_validate(payload or {})

    async def put(
        self, name: str, files: Sequence[TapiFile], *, network_id: str | None = None
    ) -> None:
        """Create or replace a container from T-API JSON documents.

        Args:
            name: The container's device name.
            files: Paths, or ``(filename, bytes)`` pairs. Several documents are
                merged into one model.
        """
        await self._transport.send(
            ops.put_container(network_id=self._network(network_id), name=name, files=files)
        )

    async def delete(self, name: str, *, network_id: str | None = None) -> None:
        """Delete one container."""
        await self._transport.send(
            ops.delete_container(network_id=self._network(network_id), container_name=name)
        )

    async def delete_all(self, network_id: str | None = None) -> None:
        """Delete every container in the network."""
        await self._transport.send(ops.delete_containers(network_id=self._network(network_id)))

    async def backdate(self, snapshot_id: str, *, network_id: str | None = None) -> None:
        """Apply container changes staged since the last snapshot to an existing one.

        The snapshot and every newer one are invalidated and need reprocessing.
        Requires both the collection-source and snapshot-invalidation
        permissions.
        """
        await self._transport.send(
            ops.backdate_containers(network_id=self._network(network_id), snapshot_id=snapshot_id)
        )
