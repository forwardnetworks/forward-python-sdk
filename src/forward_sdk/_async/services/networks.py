"""Networks and the API version."""

from __future__ import annotations

import builtins
import contextlib
import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from forward_sdk._async.services._base import AsyncService
from forward_sdk._generated.models import (
    ClassicDevice,
    HttpCredential,
    Location,
    LocationPatch,
    Network,
    NewClassicDevice,
    NewHttpCredential,
    NewLocation,
)
from forward_sdk._ops import core as ops
from forward_sdk._ops import rest
from forward_sdk.classic_devices import ClassicDeviceWithCredential
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardConfigurationError,
    ForwardNotFoundError,
    ForwardResponseError,
)

__all__ = ["AsyncNetworksService"]


class AsyncNetworksService(AsyncService):
    """Networks, and the workspaces forked from them."""

    async def list(self) -> list[Network]:
        """List every network you can see."""
        payload = await self._send_json(ops.list_networks())
        return [Network.model_validate(row) for row in payload or []]

    async def get(self, network_id: str) -> Network:
        """Read one network by id.

        Uses Forward's single-network read where it exists (after 26.10). Older
        Forward answers that path with 405 (or 404 "No endpoint"), and then the
        network list is filtered instead, so this works against both.
        """
        try:
            payload = await self._send_json(ops.get_network(network_id=network_id))
        except ForwardAPIError as exc:
            # 405: Forward 26.10 maps PATCH and DELETE on this path but not GET.
            # A 404 "No endpoint" is the same answer from a deployment that
            # maps nothing there. A plain 404 is a real missing network.
            if exc.status != 405 and not exc.endpoint_not_served:
                raise
        else:
            return Network.model_validate(payload or {})
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
        payload = await self._send_json(
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

    async def ensure_locations(
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
        existing = await self._location_rows(network)
        out: dict[str, Location] = {}
        for name, fields in wanted.items():
            current = existing.get(fields["name"].lower())
            if current is None:
                created = await self._send_json(
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
            patched = await self._send_json(
                rest.BUILDERS["patchLocation"](
                    network_id=network,
                    location_id=str(current["id"]),
                    body=LocationPatch.model_validate(changed),
                )
            )
            out[name] = Location.model_validate(patched or {})
        return out

    async def set_device_locations(
        self,
        devices: Mapping[str, str],
        *,
        network_id: str | None = None,
    ) -> None:
        """Place devices at locations, by location NAME: ``{"edge-1": "Dayton DC"}``.

        Names match as Forward matches them, trimmed and ignoring case. An
        unknown name raises ``ValueError`` before anything is sent (create it
        with :meth:`ensure_locations` first). Forward places only devices it
        knows as collection sources, so on an upload-only network register them
        first with :meth:`register_devices`:
        otherwise Forward refuses the whole update with a 400 whose
        ``error.unrecognized_devices`` names them.
        Sends one atlas update. Repeating
        it changes nothing more, but the first call pins a device that a
        dynamic (glob) rule had placed, as Forward does for any move.
        """
        if not devices:
            return
        network = self._network(network_id)
        rows = await self._location_rows(network)
        unknown = sorted({name for name in devices.values() if name.strip().lower() not in rows})
        if unknown:
            raise ValueError(f"no location named {', '.join(map(repr, unknown))} in the network")
        atlas = {device: str(rows[name.strip().lower()]["id"]) for device, name in devices.items()}
        await self._send_json(rest.BUILDERS["updateAtlas"](network_id=network, body=atlas))

    async def create_classic_device_with_api_key(
        self,
        credential: Mapping[str, Any] | Any,
        device: Mapping[str, Any] | Any,
        *,
        network_id: str | None = None,
    ) -> ClassicDeviceWithCredential:
        """Create an ``API_KEY`` HTTP credential, then a classic device that uses it.

        For connection types whose HTTP credential is an API key, such as
        ``k8s_cni_control_plane_api`` (the key is the cluster's bearer token).
        ``credential`` is a ``NewHttpCredential`` or its dict without a
        username; its ``type`` is set to ``API_KEY``. ``device`` is a
        ``NewClassicDevice`` or its dict; its HTTP credential is set to the new
        credential's id, so leave it out. A dict may name each field by its
        Python name (``login_type``) or Forward's (``loginType``); Forward is
        sent its own. Keys the model does not describe are sent unchanged.

        Not atomic: if creating the device fails, the credential stays. The
        error raised then carries it as ``error.http_credential``, so it can be
        deleted or reused. Mirrors the Go SDK's ``CreateWithAPIKey`` (v0.8.55);
        composed from two existing writes and not run against a live Forward.

        Raises:
            ValueError: ``device`` has no name or host, ``credential`` names a
                type other than ``API_KEY``, ``device`` already names an HTTP
                credential (in either spelling), or a dict names one field both
                ways. All are checked before anything is created.
            TypeError: An argument is neither a request model nor a mapping.
        """
        cred = _as_body(credential, NewHttpCredential)
        dev = _as_body(device, NewClassicDevice)
        given_type = str(cred.get("type") or "API_KEY")
        if given_type.upper() != "API_KEY":
            raise ValueError(f"this creates an API_KEY credential, not {given_type!r}")
        if not str(dev.get("name") or "").strip() or not str(dev.get("host") or "").strip():
            raise ValueError("the classic device needs a name and a host")
        if dev.get("httpCredentialId"):
            raise ValueError(
                "the device's HTTP credential is set from the new one; leave httpCredentialId out"
            )
        network = self._network(network_id)
        cred["type"] = "API_KEY"
        created = HttpCredential.model_validate(
            await self._send_json(
                rest.BUILDERS["createHttpCredential"](network_id=network, body=cred)
            )
            or {}
        )
        dev["httpCredentialId"] = created.id
        try:
            payload = await self._send_json(
                rest.BUILDERS["addClassicDevice"](network_id=network, body=dev)
            )
            made = ClassicDevice.model_validate(payload or {})
        except BaseException as error:
            # Any failure, a cancellation included, leaves the credential behind;
            # say which one (Go returns it with the error too).
            with contextlib.suppress(AttributeError):
                error.http_credential = created  # type: ignore[attr-defined]
            raise
        return ClassicDeviceWithCredential(credential=created, device=made)

    async def register_devices(
        self,
        devices: Sequence[str] | Mapping[str, str],
        *,
        network_id: str | None = None,
    ) -> builtins.list[str]:
        """Register devices as classic devices that Forward never collects from.

        For a network fed only by snapshot uploads, which has no collection
        sources: Forward places only devices it knows as classic devices, so
        :meth:`set_device_locations` refuses the rest (``error.unrecognized_devices``).
        Each is created with ``collect`` false and no credentials, in one request.
        Devices the network already has (matched by name, ignoring case) are
        left alone, so a second run sends only the read.

        ``devices`` is a list of names, each also used as its ``host``, or a
        mapping of name to host. Before anything is sent, a name Forward would
        refuse (letters, digits, ``_``, ``.`` and ``-`` only), two names that
        differ only in case, and a host that is not a valid IP address or
        hostname are all refused; a name that is not a valid hostname, such as
        ``r1.``, needs the mapping form. Forward adds the batch all or nothing,
        and also refuses a name another kind of source already uses (only
        classic devices are skipped here). Returns the names registered.
        """
        if isinstance(devices, str):
            raise ForwardConfigurationError(
                "devices takes a list of names or a name-to-host mapping"
            )
        hosts = dict(devices) if isinstance(devices, Mapping) else {name: name for name in devices}
        names = sorted(name for name in hosts if not _DEVICE_NAME.fullmatch(name))
        if names:
            raise ForwardConfigurationError(
                f"{', '.join(map(repr, names))}: Forward's device names take only letters, "
                "digits, '_', '.' and '-'"
            )
        seen: dict[str, str] = {}
        for name in hosts:
            if name.lower() in seen:
                raise ForwardConfigurationError(
                    f"{seen[name.lower()]!r} and {name!r} differ only in case; Forward treats "
                    "them as one device and would refuse the batch"
                )
            seen[name.lower()] = name
        bad = sorted(name for name, host in hosts.items() if not _host_ok(str(host)))
        if bad:
            raise ForwardConfigurationError(
                f"{', '.join(map(repr, bad))}: not a valid IP address or hostname, which Forward "
                "requires as the host; pass a name-to-host mapping"
            )
        network = self._network(network_id)
        payload = await self._send_json(rest.BUILDERS["getClassicDevices"](network_id=network))
        known = {
            str(row.get("name", "")).lower()
            for row in (payload or {}).get("devices") or []
            if isinstance(row, Mapping)
        }
        missing = [name for name in hosts if name.lower() not in known]
        if missing:
            body = [
                NewClassicDevice(name=name, host=str(hosts[name]), collect=False)
                for name in missing
            ]
            await self._send_json(rest.BUILDERS["addClassicDevices"](network_id=network, body=body))
        return missing

    async def _location_rows(self, network: str) -> builtins.dict[str, builtins.dict[str, Any]]:
        """The network's locations as raw rows, by trimmed lowercase name.

        Raw rather than parsed, so a location Forward holds without coordinates
        cannot fail the read.
        """
        payload = await self._send_json(rest.BUILDERS["getLocations"](network_id=network))
        return {
            str(row.get("name", "")).strip().lower(): dict(row)
            for row in payload or []
            if isinstance(row, Mapping) and row.get("id") is not None
        }

    async def version(self) -> Mapping[str, Any]:
        """The Forward release this instance is running."""
        return dict(await self._send_json(ops.api_version()) or {})


def _snake(row: Mapping[str, Any]) -> dict[str, Any]:
    """A location row's wire keys as the model's field names, for ``model_construct``."""
    return {
        "admin_division" if key == "adminDivision" else key: value for key, value in row.items()
    }


#: Forward's ``Hostnames.isValidHostname``: dot-separated labels of letters,
#: digits, ``_`` and ``-`` that start and end with a letter or digit, at most 253
#: characters, no trailing dot. ASCII only, case-insensitive.
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$",
    re.IGNORECASE | re.ASCII,
)
#: Forward's ``Identifiers`` rule for a device name: ``^[\w.-]+$``, Java's ASCII ``\w``.
_DEVICE_NAME = re.compile(r"^[\w.-]+$", re.ASCII)


def _host_ok(value: str) -> bool:
    """Whether Forward would take ``value`` as a host: an IP address or a hostname it accepts."""
    if "%" not in value:  # Forward refuses a scoped IPv6 address such as fe80::1%eth0
        with contextlib.suppress(ValueError):
            ipaddress.ip_address(value)
            return True
    return len(value) <= 253 and bool(_HOSTNAME.fullmatch(value))


def _as_body(value: Mapping[str, Any] | Any, model: type[BaseModel]) -> dict[str, Any]:
    """A request model or mapping as a fresh dict in the API's own field names.

    A mapping may name a field of ``model`` by its Python name or its API name;
    each is sent under the API name. A field named both ways is refused, and a
    key that is neither is sent unchanged (ADR-2611221115).
    """
    if hasattr(value, "to_api"):
        return dict(value.to_api())
    if not isinstance(value, Mapping):
        raise TypeError(f"expected a request model or a mapping, not {type(value).__name__}")
    # Assumes no Python name equals another field's API name, which holds for
    # NewHttpCredential and NewClassicDevice; such an overlap would rename silently.
    api_name = {
        name: field.alias
        for name, field in model.model_fields.items()
        if field.alias and field.alias != name
    }
    body: dict[str, Any] = {}
    for key, item in value.items():
        if key in api_name and api_name[key] in value:
            raise ValueError(
                f"{model.__name__}: {key!r} and {api_name[key]!r} name the same field; use one"
            )
        body[api_name.get(key, key)] = item
    return body
