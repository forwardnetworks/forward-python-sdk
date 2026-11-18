"""Decoding Forward's NQE library export: a ZIP holding one protobuf of query paths and sources.

Forward's export routes answer a ZIP with one entry, ``queries-export.proto``:
``NqeLibExportPB {repeated ExportedQueryPB queries = 1}``, where
``ExportedQueryPB {QueryPathPB path = 1; NqeQuerySourcePB source_code = 2}`` and
each wrapper is a message with one string field 1. The schema is small enough to
read field by field, so the SDK takes no protobuf dependency. Read from
Forward's source (``NqeLibExport``, ``NqeLibProtos.proto``); mirrors the Go SDK
v0.8.43.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from collections.abc import Iterator

from forward_sdk.errors import ForwardResponseError

__all__ = ["EXPORT_ENTRY", "MAX_EXPORT_BYTES", "decode_export", "decode_export_zip"]

#: The ZIP entry Forward writes the export to.
EXPORT_ENTRY = "queries-export.proto"
#: The most the SDK reads of an export, downloaded or decompressed: the download
#: stops as soon as it passes this, so a runaway answer cannot fill memory.
MAX_EXPORT_BYTES = 256 << 20


def _varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if at >= len(data) or shift > 63:
            raise ValueError("bad varint")
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def _fields(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Each length-delimited field (wire type 2) of one message; other wire types are skipped."""
    at = 0
    while at < len(data):
        tag, at = _varint(data, at)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            _, at = _varint(data, at)
        elif wire == 1:
            at += 8
        elif wire == 5:
            at += 4
        elif wire == 2:
            size, at = _varint(data, at)
            if at + size > len(data):
                raise ValueError("a field runs past the end of its message")
            yield field, data[at : at + size]
            at += size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if at > len(data):
            raise ValueError("a field runs past the end of its message")


def _string(message: bytes) -> str:
    """Field 1 of a one-string wrapper message."""
    for field, value in _fields(message):
        if field == 1:
            return value.decode("utf-8")
    return ""


def decode_export(data: bytes) -> dict[str, str]:
    """``queries-export.proto`` as a mapping from query path to source."""
    out: dict[str, str] = {}
    try:
        for field, query in _fields(data):
            if field != 1:
                continue
            path = source = ""
            for inner, value in _fields(query):
                if inner == 1:
                    path = _string(value)
                elif inner == 2:
                    source = _string(value)
            if not path:
                raise ValueError("an exported query has no path")
            out[path] = source
    except (ValueError, UnicodeDecodeError) as error:
        raise ForwardResponseError(
            f"the NQE export could not be decoded: {error}", model_name=EXPORT_ENTRY
        ) from error
    return out


def decode_export_zip(raw: bytes) -> dict[str, str]:
    """A whole export answer (the ZIP) as a mapping from query path to source."""
    if len(raw) > MAX_EXPORT_BYTES:
        raise ForwardResponseError("the NQE export is larger than the SDK reads", model_name="ZIP")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as error:
        raise ForwardResponseError(
            "the NQE export is not a ZIP", payload=raw[:200], model_name="ZIP"
        ) from error
    with archive:
        try:
            info = archive.getinfo(EXPORT_ENTRY)
        except KeyError:
            raise ForwardResponseError(
                f"the NQE export has no {EXPORT_ENTRY} entry", model_name="ZIP"
            ) from None
        if info.file_size > MAX_EXPORT_BYTES:
            raise ForwardResponseError(
                "the NQE export is larger than the SDK reads", model_name="ZIP"
            )
        try:
            with archive.open(info) as entry:
                data = entry.read(MAX_EXPORT_BYTES + 1)
        except (
            zipfile.BadZipFile,
            zlib.error,
            EOFError,
            NotImplementedError,
            OSError,
            RuntimeError,  # an encrypted entry
        ) as error:
            raise ForwardResponseError(
                f"the NQE export's {EXPORT_ENTRY} could not be read: {error}", model_name="ZIP"
            ) from error
    if len(data) > MAX_EXPORT_BYTES:
        raise ForwardResponseError("the NQE export is larger than the SDK reads", model_name="ZIP")
    return decode_export(data)
