"""Forward client packages, including the headless collector.

Unpublished: absent from Forward's public API description and described by
hand in ``spec/unpublished.yaml``. fwd.app serves every package to any
signed-in user, so a network-scoped API token can fetch its own collector; an
on-prem appserver serves only the Windows installer, the Linux installer and
``updates.xml``, and answers 400 for the headless packages.

The package is whatever release the server runs -- its file name says which
(``fwd-unix-26.9.0-18.tar.gz``) -- so :meth:`download` reports the name, size
and digest of what it saved rather than checking a pinned one.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from forward_sdk._async.services._base import AsyncService
from forward_sdk._ops import client_software as ops

__all__ = ["AsyncClientSoftwareService", "ClientPackage"]

HEADLESS_LINUX = "HEADLESS_LINUX"


@dataclass(frozen=True)
class ClientPackage:
    """A client package saved to disk."""

    path: Path
    #: From ``Content-Disposition``, e.g. ``fwd-unix-26.9.0-18.tar.gz``; empty if absent.
    file_name: str
    size: int
    #: Lowercase hex SHA-256 of the saved bytes.
    sha256: str


class AsyncClientSoftwareService(AsyncService):
    """Download Forward client packages."""

    async def get_client_package(self, type: str = HEADLESS_LINUX) -> AsyncIterator[bytes]:
        """Stream the client package of ``type`` in chunks."""
        async with self._transport.stream(ops.get_client_package(type=type)) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    async def download(
        self, destination: str | Path, *, type: str = HEADLESS_LINUX
    ) -> ClientPackage:
        """Save the client package of ``type`` and report its name, size and digest.

        ``destination`` is a file path, or an existing directory to save into under
        the server's file name. The file is written to a temporary name beside it
        and renamed only once complete, so a failed download never leaves a
        truncated archive where the collector is expected.
        """
        target = Path(destination)
        digest = hashlib.sha256()
        size = 0
        async with self._transport.stream(ops.get_client_package(type=type)) as response:
            file_name = _disposition_filename(response.headers.get("content-disposition", ""))
            if target.is_dir():
                if not file_name:
                    raise ValueError(
                        "the server sent no file name; pass a file path, not a directory"
                    )
                target = target / file_name
            partial = target.with_name(target.name + ".part")
            try:
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                partial.replace(target)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        return ClientPackage(path=target, file_name=file_name, size=size, sha256=digest.hexdigest())


def _disposition_filename(header: str) -> str:
    if not header:
        return ""
    message = Message()
    message["content-disposition"] = header
    name = message.get_filename() or ""
    return Path(name).name  # never let a header steer the write outside the directory
