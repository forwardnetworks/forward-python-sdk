"""Reading a snapshot package: its ``sources.json`` index, and its logs with credentials masked.

A snapshot package is the ZIP ``client.snapshots.export`` returns. At its top
level Forward writes ``sources.json``: one entry per collection source (a
classic device, a controller, a cloud account, a Kubernetes cluster). The reason
to read it is ``relatedDevicesWithoutSnapshots``, the devices the parser
generated for a source that have no snapshot of their own and so never appear
as devices. No REST route reports them, and they are the only explanation for a
snapshot that processed with zero devices::

    from forward_sdk.snapshot_package import read_sources

    sources = read_sources("package.zip")
    print(sources.devices_without_snapshots())

Preview: the entry's shape was read from a real package by the Go SDK (v0.8.57),
not from Forward's source, so only the fields seen are named and ``raw`` keeps
each entry whole.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forward_sdk._ports import ForwardConfigurationError

__all__ = [
    "SnapshotPackageSource",
    "SnapshotPackageSources",
    "parse_sources",
    "read_sources",
    "redact_log",
]

_FIELDS = {
    "name": "name",
    "requestedDeviceName": "requested_device_name",
    "vendor": "vendor",
    "deviceType": "device_type",
    "createdBy": "created_by",
    "error": "error",
    "customCommandError": "custom_command_error",
    "collectionIp": "collection_ip",
    "collectionPort": "collection_port",
    "collectionInstantMillis": "collection_instant_millis",
    "clientPackageVersion": "client_package_version",
}


@dataclass(frozen=True)
class SnapshotPackageSource:
    """One collection source in ``sources.json``.

    Attributes:
        name: The source's name.
        related_devices_without_snapshots: Devices generated for this source
            that have no snapshot of their own.
        files: The source's package files, named ``"<source>,<file>"``.
        created_by: The connection type that created the source, such as
            ``k8s_cni_control_plane_api``.
        error: Forward's collection error enum; ``"NONE"`` when clean.
        raw: The whole entry as Forward wrote it, unknown fields included.
    """

    __hash__ = None  # type: ignore[assignment]  # it holds a dict and lists

    name: str = ""
    requested_device_name: str | None = None
    related_devices_without_snapshots: tuple[str, ...] = ()
    vendor: str | None = None
    device_type: str | None = None
    files: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    created_by: str | None = None
    error: str | None = None
    custom_command_error: str | None = None
    collection_ip: str | None = None
    collection_port: int | None = None
    collection_instant_millis: int | None = None
    client_package_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotPackageSources:
    """The decoded ``sources.json``."""

    __hash__ = None  # type: ignore[assignment]

    sources: tuple[SnapshotPackageSource, ...] = ()

    def source(self, name: str) -> SnapshotPackageSource | None:
        """The entry named ``name``, compared case-insensitively, or ``None``."""
        wanted = name.casefold()
        return next((s for s in self.sources if s.name.casefold() == wanted), None)

    def devices_without_snapshots(self) -> list[str]:
        """Every device any source lists as without a snapshot, sorted and de-duplicated."""
        return sorted({name for s in self.sources for name in s.related_devices_without_snapshots})


def parse_sources(data: str | bytes | Mapping[str, Any]) -> SnapshotPackageSources:
    """Decode ``sources.json`` (``{"sources": [...]}``) from its text or parsed form.

    Raises:
        ForwardConfigurationError: The text is not JSON, or an entry is not an object.
    """
    if isinstance(data, Mapping):
        document: Any = data
    else:
        try:
            document = json.loads(data)
        except ValueError as error:
            raise ForwardConfigurationError(f"sources.json is not JSON: {error}") from error
    if not isinstance(document, Mapping):
        raise ForwardConfigurationError("sources.json is not a JSON object")
    entries = []
    for entry in document.get("sources") or []:
        if not isinstance(entry, Mapping):
            raise ForwardConfigurationError("a sources.json entry is not a JSON object")
        known = {attr: entry[key] for key, attr in _FIELDS.items() if entry.get(key) is not None}
        entries.append(
            SnapshotPackageSource(
                **known,
                related_devices_without_snapshots=tuple(
                    entry.get("relatedDevicesWithoutSnapshots") or ()
                ),
                files=tuple(entry.get("files") or ()),
                states=tuple(entry.get("states") or ()),
                raw=dict(entry),
            )
        )
    return SnapshotPackageSources(sources=tuple(entries))


def read_sources(path: str | Path) -> SnapshotPackageSources:
    """Open a snapshot package ZIP and decode its top-level ``sources.json``.

    Raises:
        FileNotFoundError: No such file.
        ForwardConfigurationError: The file is not a ZIP, or has no ``sources.json``.
    """
    archive = Path(path)
    if not archive.is_file():
        raise FileNotFoundError(f"snapshot package not found: {archive}")
    try:
        with zipfile.ZipFile(archive) as zipped:
            try:
                text = zipped.read("sources.json")
            except KeyError:
                raise ForwardConfigurationError(
                    f"{archive.name} has no sources.json at its top level"
                ) from None
    except zipfile.BadZipFile as error:
        raise ForwardConfigurationError(f"{archive.name} is not a ZIP archive") from error
    return parse_sources(text)


_AUTH_HEADER = re.compile(r"(?ai)((?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?)(.*)")
_BEARER_OR_BASIC = re.compile(r"(?ai)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
#: Lines end at "\n" only, as in Go: a lone "\r" stays inside the line, so a
#: header that continues past one is still masked to the end.
_LINES = re.compile(r"(?<=\n)")


def redact_log(text: str) -> str:
    """A package log (``cli-execution.log`` and the like) with credentials masked.

    Masks everything after ``Authorization:`` or ``Proxy-Authorization:`` on a
    line, and any ``Bearer <token>`` or ``Basic <token>`` elsewhere; line endings
    are kept. Forward writes a Kubernetes bearer token into
    ``cli-execution.log`` unmasked, so run this before a package log is stored,
    shown or made into a fixture. It masks the shapes it knows; it does not
    prove that no secret remains. The same patterns as the Go SDK.
    """
    out = []
    for line in _LINES.split(text):
        if not line:
            continue
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        body = _AUTH_HEADER.sub(r"\1<redacted>", body)
        body = _BEARER_OR_BASIC.sub(r"\1 <redacted>", body)
        out.append(body + ending)
    return "".join(out)
