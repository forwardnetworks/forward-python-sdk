# Generated from tests/_async/test_client_software.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Forward client packages: the headless collector download.

Unpublished endpoint (``GET /api/software/client``), verified against fwd.app
26.9.0-18: a network-scoped API token receives ``fwd-unix-<release>.tar.gz``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardBadRequestError
from tests.conftest import Recorder, error_response

pytestmark = pytest.mark.anyio

ROUTE = "/api/software/client"
PAYLOAD = b"fwd-headless" * 4096
DISPOSITION = {
    "content-type": "application/gzip",
    "content-disposition": 'attachment; filename="fwd-unix-26.9.0-18.tar.gz"',
}


def make_client(recorder: Recorder, **overrides: Any) -> ForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return ForwardClient("https://forward.test", **settings)


class TestClientSoftware:
    def test_streams_the_requested_package(self, recorder: Recorder) -> None:
        recorder.add("GET", ROUTE, httpx.Response(200, content=PAYLOAD, headers=DISPOSITION))
        with make_client(recorder) as client:
            chunks = [c for c in client.client_software.get_client_package("HEADLESS_LINUX")]

        assert b"".join(chunks) == PAYLOAD
        assert recorder.query_for()["type"] == ["HEADLESS_LINUX"]

    def test_download_into_a_directory_uses_the_servers_file_name(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        recorder.add("GET", ROUTE, httpx.Response(200, content=PAYLOAD, headers=DISPOSITION))
        with make_client(recorder) as client:
            package = client.client_software.download(tmp_path)

        assert package.path == tmp_path / "fwd-unix-26.9.0-18.tar.gz"
        assert package.path.read_bytes() == PAYLOAD
        assert package.file_name == "fwd-unix-26.9.0-18.tar.gz"
        assert package.size == len(PAYLOAD)
        assert package.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert recorder.query_for()["type"] == ["HEADLESS_LINUX"]
        assert not list(tmp_path.glob("*.part"))

    def test_download_to_a_file_path(self, recorder: Recorder, tmp_path: Path) -> None:
        recorder.add("GET", ROUTE, httpx.Response(200, content=PAYLOAD, headers=DISPOSITION))
        target = tmp_path / "collector.tar.gz"
        with make_client(recorder) as client:
            package = client.client_software.download(target)

        assert package.path == target
        assert target.read_bytes() == PAYLOAD

    def test_a_hostile_file_name_cannot_escape_the_directory(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        headers = {"content-disposition": 'attachment; filename="../../etc/fwd.tar.gz"'}
        recorder.add("GET", ROUTE, httpx.Response(200, content=PAYLOAD, headers=headers))
        with make_client(recorder) as client:
            package = client.client_software.download(tmp_path)

        assert package.path == tmp_path / "fwd.tar.gz"

    def test_an_unsupported_type_raises_and_leaves_no_file(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        # What an on-prem appserver answers for the headless packages.
        recorder.add("GET", ROUTE, error_response(400, "Unsupported package type: HEADLESS_LINUX"))
        target = tmp_path / "collector.tar.gz"
        with make_client(recorder) as client:
            with pytest.raises(ForwardBadRequestError):
                client.client_software.download(target)

        assert list(tmp_path.iterdir()) == []

    def test_a_directory_needs_a_server_file_name(self, recorder: Recorder, tmp_path: Path) -> None:
        recorder.add("GET", ROUTE, httpx.Response(200, content=PAYLOAD))
        with make_client(recorder) as client:
            with pytest.raises(ValueError, match="no file name"):
                client.client_software.download(tmp_path)

        assert list(tmp_path.iterdir()) == []
