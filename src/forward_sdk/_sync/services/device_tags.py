# Generated from src/forward_sdk/_async/services/device_tags.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Device tags, and the devices they are applied to."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._ops import core as ops
from forward_sdk._sync.services._base import Service

__all__ = ["DeviceTagsService"]


class DeviceTagsService(Service):
    """Read and change the tags applied to devices.

    Tags are how most integrations scope a sync, so both the tag list and each
    tag's device membership are available.
    """

    def list(
        self,
        network_id: str | None = None,
        *,
        with_devices: bool = False,
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List every tag, optionally with the devices carrying each one.

        ``snapshot_id`` reads the devices that carried each tag as of that
        snapshot rather than now; only the form with devices takes one, so it
        needs ``with_devices=True``.
        """
        snapshot_id = _snapshot(snapshot_id)
        if snapshot_id is not None and not with_devices:
            raise ValueError(
                "snapshot_id reads the devices as of a snapshot; pass with_devices=True"
            )
        resolved = self._network(network_id)
        spec = (
            ops.list_device_tags_with_devices(network_id=resolved, snapshot_id=snapshot_id)
            if with_devices
            else ops.list_device_tags(network_id=resolved)
        )
        payload = self._send_json(spec)
        # Forward wraps the list: {"tags": [...]}. Reading the payload as a
        # list returned its keys, so callers got ["tags"].
        if isinstance(payload, dict):
            return list(payload.get("tags") or [])
        return list(payload or [])

    def get(
        self, tag_name: str, *, network_id: str | None = None, with_devices: bool = False
    ) -> dict[str, Any]:
        """Fetch one tag."""
        resolved = self._network(network_id)
        spec = (
            ops.get_device_tag_with_devices(network_id=resolved, tag_name=tag_name)
            if with_devices
            else ops.get_device_tag(network_id=resolved, tag_name=tag_name)
        )
        return dict(self._send_json(spec) or {})

    def create(self, tag: Mapping[str, Any], *, network_id: str | None = None) -> dict[str, Any]:
        """Create a tag."""
        payload = self._send_json(ops.add_device_tag(network_id=self._network(network_id), tag=tag))
        return dict(payload or {})

    def create_many(
        self, tags: Sequence[Mapping[str, Any]], *, network_id: str | None = None
    ) -> Any:
        """Create several tags in one request."""
        return self._send_json(ops.add_device_tags(network_id=self._network(network_id), tags=tags))

    def update(
        self, tag_name: str, changes: Mapping[str, Any], *, network_id: str | None = None
    ) -> dict[str, Any]:
        payload = self._send_json(
            ops.update_device_tag(
                network_id=self._network(network_id), tag_name=tag_name, changes=changes
            )
        )
        return dict(payload or {})

    def delete(self, tag_name: str, *, network_id: str | None = None) -> None:
        self._send_json(
            ops.delete_device_tag(network_id=self._network(network_id), tag_name=tag_name)
        )

    def add_to_devices(
        self, tag_name: str, devices: Sequence[str], *, network_id: str | None = None
    ) -> Any:
        """Apply one tag to a set of devices."""
        return self._send_json(
            ops.add_tag_to_devices(
                network_id=self._network(network_id), tag_name=tag_name, devices=devices
            )
        )

    def remove_from_devices(
        self, tag_name: str, devices: Sequence[str], *, network_id: str | None = None
    ) -> Any:
        """Remove one tag from a set of devices."""
        return self._send_json(
            ops.remove_tag_from_devices(
                network_id=self._network(network_id), tag_name=tag_name, devices=devices
            )
        )

    def add_tags_to_devices(
        self, body: Any, *, network_id: str | None = None, snapshot_id: str | None = None
    ) -> Any:
        """Apply several tags to several devices in one request.

        ``snapshot_id`` makes the change take effect from that snapshot, so an
        NQE read of it (``device.tagNames``) shows it at once; without one it
        shows from the next snapshot. With a snapshot, Forward checks the device
        names against that snapshot's devices and endpoints rather than the
        network's collection sources.
        """
        return self._send_json(
            ops.add_tags_to_devices(
                network_id=self._network(network_id), body=body, snapshot_id=_snapshot(snapshot_id)
            )
        )

    def remove_tags_from_devices(
        self, body: Any, *, network_id: str | None = None, snapshot_id: str | None = None
    ) -> Any:
        """Remove several tags from several devices in one request.

        ``snapshot_id`` makes the change take effect from that snapshot, so an
        NQE read of it (``device.tagNames``) shows it at once; without one it
        shows from the next snapshot. With a snapshot, Forward checks the device
        names against that snapshot's devices and endpoints rather than the
        network's collection sources.
        """
        return self._send_json(
            ops.remove_tags_from_devices(
                network_id=self._network(network_id), body=body, snapshot_id=_snapshot(snapshot_id)
            )
        )

    def remove_all_from_devices(
        self,
        devices: Sequence[str],
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> Any:
        """Strip every tag from a set of devices.

        ``snapshot_id`` makes the change take effect from that snapshot, so an
        NQE read of it (``device.tagNames``) shows it at once; without one it
        shows from the next snapshot. With a snapshot, Forward checks the device
        names against that snapshot's devices and endpoints rather than the
        network's collection sources.
        """
        return self._send_json(
            ops.remove_all_tags_from_devices(
                network_id=self._network(network_id),
                devices=devices,
                snapshot_id=_snapshot(snapshot_id),
            )
        )

    def replace_all_for_devices(
        self, body: Any, *, network_id: str | None = None, snapshot_id: str | None = None
    ) -> Any:
        """Replace the full tag set of the given devices.

        ``snapshot_id`` makes the change take effect from that snapshot, so an
        NQE read of it (``device.tagNames``) shows it at once; without one it
        shows from the next snapshot. With a snapshot, Forward checks the device
        names against that snapshot's devices and endpoints rather than the
        network's collection sources.
        """
        return self._send_json(
            ops.replace_all_tags_for_devices(
                network_id=self._network(network_id), body=body, snapshot_id=_snapshot(snapshot_id)
            )
        )


def _snapshot(snapshot_id: str | None) -> str | None:
    """A snapshot id as Forward reads it; blank is refused rather than sent as "snapshotId="."""
    if snapshot_id is None:
        return None
    cleaned = str(snapshot_id).strip()
    if not cleaned:
        raise ValueError("snapshot_id is blank; pass a snapshot id or leave it out")
    return cleaned
