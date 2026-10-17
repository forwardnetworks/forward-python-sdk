# Generated from src/forward_sdk/_async/services/client_software.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

from forward_sdk._generated.models import SysDeploymentArtifact
from forward_sdk._ops import client_software as ops
from forward_sdk._ops import rest
from forward_sdk._sync.services._base import Service

__all__ = ["ClientPackage", "ClientSoftwareService"]

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


class ClientSoftwareService(Service):
    """Download Forward client packages."""

    def get_client_package(self, type: str = HEADLESS_LINUX) -> Iterator[bytes]:
        """Stream the client package of ``type`` in chunks."""
        with self._transport.stream(ops.get_client_package(type=type)) as response:
            for chunk in response.iter_bytes():
                yield chunk

    def download(self, destination: str | Path, *, type: str = HEADLESS_LINUX) -> ClientPackage:
        """Save the client package of ``type`` and report its name, size and digest.

        ``destination`` is a file path, or an existing directory to save into under
        the server's file name. The file is written to a temporary name beside it
        and renamed only once complete, so a failed download never leaves a
        truncated archive where the collector is expected.
        """
        target = Path(destination)
        digest = hashlib.sha256()
        size = 0
        with self._transport.stream(ops.get_client_package(type=type)) as response:
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
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                partial.replace(target)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        return ClientPackage(path=target, file_name=file_name, size=size, sha256=digest.hexdigest())

    def deployment_artifacts(self, type: str | None = None) -> list[SysDeploymentArtifact]:
        """The appliance images and other artifacts Forward offers for download.

        Unpublished, and it needs the SOFTWARE_CENTRAL organization property: an
        organization without it is refused with 403 (seen on a hosted one).
        """
        payload = self._send_json(rest.BUILDERS["listDeploymentArtifacts"](type=type))
        rows = payload if isinstance(payload, list) else (payload or {}).get("artifacts") or []
        return [SysDeploymentArtifact.model_validate(row) for row in rows]

    def deployment_artifact_url(
        self, artifact_id: str, version_id: str | None = None
    ) -> Mapping[str, Any]:
        """A short-lived download URL for one artifact (Forward's reply, unmodelled)."""
        payload = self._send_json(
            rest.BUILDERS["getDeploymentArtifactUrl"](
                artifact_id=artifact_id, version_id=version_id
            )
        )
        return dict(payload or {})


def _disposition_filename(header: str) -> str:
    if not header:
        return ""
    message = Message()
    message["content-disposition"] = header
    name = message.get_filename() or ""
    return Path(name).name  # never let a header steer the write outside the directory
