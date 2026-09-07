"""Devices in a snapshot."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._generated.models import Device
from forward_sdk._ops import core as ops

__all__ = ["AsyncDevicesService"]

DEFAULT_PAGE_SIZE = 500

#: Optional detail Forward omits unless asked for.
WITH_TAGS = "tags"
WITH_LOCATION = "locationId"


class AsyncDevicesService(AsyncService):
    """Read the devices Forward collected.

    Note that ``tags`` and ``locationId`` are absent from a device unless
    requested through ``with_``. Their absence means "not requested", never
    "none set".
    """

    async def list(
        self,
        network_id: str | None = None,
        *,
        snapshot_id: str | None = None,
        skip: int | None = None,
        limit: int | None = None,
        with_: Sequence[str] | None = None,
        **filters: Any,
    ) -> list[Device]:
        """List devices, optionally filtered by vendor, model, platform and so on."""
        payload = await self._send_json(
            ops.list_devices(
                network_id=self._network(network_id),
                snapshot_id=self._snapshot(snapshot_id),
                skip=skip,
                limit=limit,
                with_=with_,
                **filters,
            )
        )
        return [Device.model_validate(row) for row in payload or []]

    async def iter(
        self,
        network_id: str | None = None,
        *,
        snapshot_id: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        with_: Sequence[str] | None = None,
        **filters: Any,
    ) -> AsyncIterator[Device]:
        """Iterate every device, fetching a page at a time.

        Useful for large networks, where one unbounded response would be slow
        to arrive and large to hold.
        """
        skip = 0
        while True:
            page = await self.list(
                network_id,
                snapshot_id=snapshot_id,
                skip=skip,
                limit=page_size,
                with_=with_,
                **filters,
            )
            for device in page:
                yield device
            if len(page) < page_size:
                return
            skip += len(page)

    async def get(
        self,
        device_name: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        with_: Sequence[str] | None = None,
    ) -> Device:
        """Fetch one device by name."""
        payload = await self._send_json(
            ops.get_device(
                network_id=self._network(network_id),
                device_name=device_name,
                snapshot_id=self._snapshot(snapshot_id),
                with_=with_,
            )
        )
        return Device.model_validate(payload or {})

    async def files(
        self,
        device_name: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> Any:
        """List the files collected from a device."""
        return await self._send_json(
            ops.list_device_files(
                network_id=self._network(network_id),
                device_name=device_name,
                snapshot_id=self._snapshot(snapshot_id),
            )
        )

    async def file(
        self,
        device_name: str,
        file_name: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Fetch one collected file, such as a running configuration."""
        response = await self._transport.send(
            ops.get_device_file(
                network_id=self._network(network_id),
                device_name=device_name,
                file_name=file_name,
                snapshot_id=self._snapshot(snapshot_id),
            )
        )
        return response.text

    async def missing(
        self, network_id: str | None = None, *, snapshot_id: str | None = None
    ) -> Any:
        """List devices the network expects but the snapshot does not contain."""
        return await self._send_json(
            ops.list_missing_devices(
                network_id=self._network(network_id),
                snapshot_id=self._snapshot(snapshot_id),
            )
        )
