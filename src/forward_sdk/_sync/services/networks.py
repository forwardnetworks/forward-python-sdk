# Generated from src/forward_sdk/_async/services/networks.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Networks and the API version."""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from typing import Any

from forward_sdk._generated.models import Location, LocationPatch, Network, NewLocation
from forward_sdk._ops import core as ops
from forward_sdk._ops import rest
from forward_sdk._sync.services._base import Service
from forward_sdk.errors import ForwardAPIError, ForwardNotFoundError, ForwardResponseError

__all__ = ["NetworksService"]


class NetworksService(Service):
    """Networks, and the workspaces forked from them."""

    def list(self) -> list[Network]:
        """List every network you can see."""
        payload = self._send_json(ops.list_networks())
        return [Network.model_validate(row) for row in payload or []]

    def get(self, network_id: str) -> Network:
        """Read one network by id.

        Uses Forward's single-network read where it exists (after 26.10). Older
        Forward answers that path with 405 (or 404 "No endpoint"), and then the
        network list is filtered instead, so this works against both.
        """
        try:
            payload = self._send_json(ops.get_network(network_id=network_id))
        except ForwardAPIError as exc:
            # 405: Forward 26.10 maps PATCH and DELETE on this path but not GET.
            # A 404 "No endpoint" is the same answer from a deployment that
            # maps nothing there. A plain 404 is a real missing network.
            if exc.status != 405 and not exc.endpoint_not_served:
                raise
        else:
            return Network.model_validate(payload or {})
        for network in self.list():
            if network.id == network_id:
                return network
        raise ForwardNotFoundError(f"no network with id {network_id!r}", status=404)

    def create(self, name: str, *, note: str | None = None) -> Network:
        """Create a network.

        Forward's create endpoint takes only a name, so a note is applied as a
        follow-up update.
        """
        payload = self._send_json(ops.create_network(name=name))
        network = Network.model_validate(payload or {})
        if note is not None and network.id:
            return self.update(network.id, note=note)
        return network

    def update(self, network_id: str, **changes: Any) -> Network:
        """Change a network's name, note or retention."""
        payload = self._send_json(
            ops.update_network(network_id=self._network(network_id), changes=changes)
        )
        return Network.model_validate(payload or {})

    def delete(self, network_id: str) -> None:
        """Delete a network and everything in it."""
        self._send_json(ops.delete_network(network_id=self._network(network_id)))

    def create_workspace(
        self,
        network_id: str | None = None,
        *,
        name: str | None = None,
        retention_days: int | None = None,
        devices: Sequence[str] | None = None,
        cloud_accounts: Sequence[str] | None = None,
        vcenters: Sequence[str] | None = None,
        omissions: Sequence[str] | None = None,
    ) -> Network:
        """Fork a network into a workspace for what-if changes.

        Omit ``devices``, ``cloud_accounts`` and ``vcenters`` to fork every source in the
        parent network. Pass any of them to scope the workspace to a subset instead --
        useful for a change window that only touches a handful of devices. At least one
        of the three is required by the API if any is passed at all.
        """
        payload = self._send_json(
            ops.create_workspace(
                network_id=self._network(network_id),
                name=name,
                retention_days=retention_days,
                devices=devices,
                cloud_accounts=cloud_accounts,
                vcenters=vcenters,
                omissions=omissions,
            )
        )
        return Network.model_validate(payload or {})

    # -- locations ---------------------------------------------------------

    def ensure_locations(
        self,
        locations: Mapping[str, NewLocation | Mapping[str, Any]],
        *,
        network_id: str | None = None,
    ) -> dict[str, Location]:
        """Make sure each named location exists with these fields; idempotent by name.

        ``locations`` maps a location's name to its fields (``lat`` and ``lng``
        at least, plus ``city``, ``country``, ``admin_division``), as a mapping
        (Python or wire names) or a :class:`~forward_sdk.models.NewLocation`.
        Every entry is checked before anything is sent; a bad one raises
        ``ValueError``. Names match as Forward matches them, trimmed and
        ignoring case. A name the network lacks is created; one it has is
        patched only in the fields that differ, so a second run sends only the
        read. Returns every named location as Forward holds it, by the name
        given.
        """
        wanted: dict[str, dict[str, Any]] = {}
        for name, given in locations.items():
            fields = given.to_api() if isinstance(given, NewLocation) else dict(given)
            fields["name"] = name.strip()
            try:
                wanted[name] = NewLocation.model_validate(fields).to_api()
            except ForwardResponseError as error:
                raise ValueError(f"location {name!r}: {error.__cause__ or error}") from None
            for key in ("city", "country", "adminDivision"):
                if isinstance(wanted[name].get(key), str):
                    wanted[name][key] = wanted[name][key].strip()

        network = self._network(network_id)
        existing = self._location_rows(network)
        out: dict[str, Location] = {}
        for name, fields in wanted.items():
            current = existing.get(fields["name"].lower())
            if current is None:
                created = self._send_json(
                    rest.BUILDERS["addLocation"](network_id=network, body=fields)
                )
                out[name] = Location.model_validate(created or {})
                continue
            changed = {
                key: value
                for key, value in fields.items()
                if key not in ("id", "name") and current.get(key) != value
            }
            if not changed:
                out[name] = Location.model_construct(**_snake(current))
                continue
            patched = self._send_json(
                rest.BUILDERS["patchLocation"](
                    network_id=network,
                    location_id=str(current["id"]),
                    body=LocationPatch.model_validate(changed),
                )
            )
            out[name] = Location.model_validate(patched or {})
        return out

    def set_device_locations(
        self,
        devices: Mapping[str, str],
        *,
        network_id: str | None = None,
    ) -> None:
        """Place devices at locations, by location NAME: ``{"edge-1": "Dayton DC"}``.

        Names match as Forward matches them, trimmed and ignoring case. An
        unknown name raises ``ValueError`` before anything is sent (create it
        with :meth:`ensure_locations` first). Sends one atlas update. Repeating
        it changes nothing more, but the first call pins a device that a
        dynamic (glob) rule had placed, as Forward does for any move.
        """
        if not devices:
            return
        network = self._network(network_id)
        rows = self._location_rows(network)
        unknown = sorted({name for name in devices.values() if name.strip().lower() not in rows})
        if unknown:
            raise ValueError(f"no location named {', '.join(map(repr, unknown))} in the network")
        atlas = {device: str(rows[name.strip().lower()]["id"]) for device, name in devices.items()}
        self._send_json(rest.BUILDERS["updateAtlas"](network_id=network, body=atlas))

    def _location_rows(self, network: str) -> builtins.dict[str, builtins.dict[str, Any]]:
        """The network's locations as raw rows, by trimmed lowercase name.

        Raw rather than parsed, so a location Forward holds without coordinates
        cannot fail the read.
        """
        payload = self._send_json(rest.BUILDERS["getLocations"](network_id=network))
        return {
            str(row.get("name", "")).strip().lower(): dict(row)
            for row in payload or []
            if isinstance(row, Mapping) and row.get("id") is not None
        }

    def version(self) -> Mapping[str, Any]:
        """The Forward release this instance is running."""
        return dict(self._send_json(ops.api_version()) or {})


def _snake(row: Mapping[str, Any]) -> dict[str, Any]:
    """A location row's wire keys as the model's field names, for ``model_construct``."""
    return {
        "admin_division" if key == "adminDivision" else key: value for key, value in row.items()
    }
