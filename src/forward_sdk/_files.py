"""Opening upload files for the transport.

Kept apart from the request builders, which perform no I/O, so that a request
can be described and tested without touching the disk. The transport opens the
files only when it is about to send, and closes them when the call ends.
"""

from __future__ import annotations

from typing import BinaryIO

from forward_sdk._ops import FileUpload


class CountingReader:
    """A binary file that reports how many bytes were read from it.

    httpx streams a multipart file part by calling ``read`` in chunks, so counting
    reads counts bytes handed to the network. It also needs ``fileno`` or
    ``seek``/``tell`` to learn the length without reading, and ``seek(0)`` before
    it starts, none of which count.
    """

    def __init__(self, handle: BinaryIO, on_bytes: object) -> None:
        self._handle = handle
        self._on_bytes = on_bytes

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        if data and callable(self._on_bytes):
            self._on_bytes(len(data))
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        return self._handle.tell()

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()


def open_upload(upload: FileUpload) -> BinaryIO:
    """Open one upload for streaming, counting bytes if it asked to be counted."""
    handle = upload.path.open("rb")
    if upload.on_bytes is None:
        return handle
    return CountingReader(handle, upload.on_bytes)  # type: ignore[return-value]
