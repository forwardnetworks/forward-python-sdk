"""``forward_sdk.nqe.export``: Forward's library export ZIP and the protobuf inside it."""

from __future__ import annotations

import io
import zipfile

import pytest

from forward_sdk.errors import ForwardResponseError
from forward_sdk.nqe.export import EXPORT_ENTRY, decode_export, decode_export_zip


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _wrapper(text: str) -> bytes:
    return _field(1, text.encode())


def export(*queries: tuple[str, str]) -> bytes:
    """NqeLibExportPB, written independently of the decoder from the schema in Forward's source."""
    body = b""
    for path, source in queries:
        query = _field(1, _wrapper(path)) + _field(2, _wrapper(source))
        body += _field(1, query)
    return body


def zipped(data: bytes, name: str = EXPORT_ENTRY) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


def test_paths_map_to_their_sources() -> None:
    long = "foreach d in network.devices\n" * 20  # a length over 127 needs a two-byte varint
    raw = zipped(export(("/A/one", "select {}"), ("/A/two", long), ("/é", "x")))
    assert decode_export_zip(raw) == {"/A/one": "select {}", "/A/two": long, "/é": "x"}


def test_unknown_fields_and_wire_types_are_skipped() -> None:
    varint_field = _varint(3 << 3 | 0) + _varint(150)
    fixed64 = _varint(4 << 3 | 1) + b"\x00" * 8
    data = varint_field + fixed64 + export(("/q", "src")) + _field(9, b"ignored")
    assert decode_export(data) == {"/q": "src"}


def test_an_empty_export_is_empty() -> None:
    assert decode_export_zip(zipped(b"")) == {}


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (b"not a zip", "not a ZIP"),
        (zipped(b"", name="other.bin"), "no queries-export.proto"),
        (zipped(_field(1, _field(2, _wrapper("src")))), "has no path"),
        (zipped(_varint(1 << 3 | 2) + _varint(50) + b"short"), "past the end"),
    ],
)
def test_a_broken_export_is_a_forward_error(raw: bytes, match: str) -> None:
    with pytest.raises(ForwardResponseError, match=match):
        decode_export_zip(raw)


def test_corrupt_entry_data_is_a_forward_error() -> None:
    raw = bytearray(zipped(export(("/q", "select {}" * 10))))
    # Flip a byte of the stored entry's data, past the local header, so the CRC no longer matches.
    raw[60] ^= 0xFF
    with pytest.raises(ForwardResponseError):
        decode_export_zip(bytes(raw))
