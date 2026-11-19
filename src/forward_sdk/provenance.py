"""Tying a snapshot back to the collector output it was made from.

Before uploading a snapshot ZIP, :func:`describe_archive` hashes it and lists
what it holds, and :meth:`ArchiveProvenance.note` turns that into a short note
to upload with it, so anyone holding the ZIP can confirm which snapshot it
became (and the reverse)::

    from forward_sdk.provenance import describe_archive

    archive = describe_archive("collection.zip")
    snapshot = client.snapshots.upload("collection.zip", note=archive.note("pre-change"))

Files are grouped by device from names of the form ``"<device>,<file>"``, the
naming Forward lists a snapshot's raw files under and the AutoCon6 workshop saw
in its collector's archives; the directory part of an entry's path is ignored.
Any other name is listed in ``other_files``. Asked for by the AutoCon6 workshop.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from forward_sdk.errors import ForwardConfigurationError

__all__ = ["ArchiveProvenance", "describe_archive"]

_CHUNK = 1 << 20


@dataclass(frozen=True)
class ArchiveProvenance:
    """What a snapshot ZIP is: its name, size, SHA-256 and contents."""

    __hash__ = None  # type: ignore[assignment]  # it holds a dict, so it is not hashable

    name: str
    size: int
    sha256: str
    #: Device name to the file names it contributed, in archive order.
    devices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Entries not named ``"<device>,<file>"`` (directories excluded).
    other_files: tuple[str, ...] = ()

    @property
    def file_count(self) -> int:
        return sum(len(files) for files in self.devices.values()) + len(self.other_files)

    def note(self, prefix: str | None = None) -> str:
        """A one-line snapshot note naming the archive and its SHA-256.

        ``prefix`` (a change id, say) goes first. The full hash is kept; the
        rest is a count, so the note stays short.
        """
        body = (
            f"{self.name} sha256:{self.sha256} {self.size} bytes, "
            f"{_plural(len(self.devices), 'device')}, {_plural(self.file_count, 'file')}"
        )
        return f"{prefix.strip()} | {body}" if prefix and prefix.strip() else body


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def describe_archive(path: str | Path) -> ArchiveProvenance:
    """Hash a snapshot ZIP and list its devices and files, without unpacking it.

    The hash is streamed, so the archive's size does not matter to memory.

    Raises:
        FileNotFoundError: No such file.
        ForwardConfigurationError: The file is not a ZIP.
    """
    archive = Path(path)
    if not archive.is_file():
        raise FileNotFoundError(f"snapshot archive not found: {archive}")
    digest = hashlib.sha256()
    size = 0
    with archive.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    try:
        with zipfile.ZipFile(archive) as zipped:
            names = [info.filename for info in zipped.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as error:
        raise ForwardConfigurationError(f"{archive.name} is not a ZIP archive") from error
    devices: dict[str, list[str]] = {}
    other: list[str] = []
    for entry in names:
        leaf = entry.rsplit("/", 1)[-1]
        device, comma, rest = leaf.partition(",")
        if comma and device and rest:
            devices.setdefault(device, []).append(rest)
        else:
            other.append(entry)
    return ArchiveProvenance(
        name=archive.name,
        size=size,
        sha256=digest.hexdigest(),
        devices={device: tuple(files) for device, files in devices.items()},
        other_files=tuple(other),
    )
