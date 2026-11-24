"""Working with NQE rows on the client: decoding them into types, and diffing two sets.

Forward's NQE diff (``POST /nqe-diffs/{before}/{after}``) takes only a library
query id: it has no field for query source or parameters. :func:`diff_rows`
compares two result sets here instead, keyed on columns the caller names, and
returns entries in the shape Forward's diff does. ``nqe.diff_source()`` on the
client runs a query on both snapshots and calls it.

Mirrors the Go SDK v0.8.30 (``DecodeNQE``, ``DiffNQERows``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from forward_sdk._generated.models import NqeDiffEntry
from forward_sdk.nqe.cells import flatten_cells

__all__ = ["decode_rows", "diff_rows", "flatten_cells", "strip_enum_text"]

T = TypeVar("T")

#: An NQE enum value as Forward writes it in JSON output: ``Vendor.CISCO``,
#: ``OperStatus.UP``.
_ENUM_TEXT = re.compile(r"^[A-Z][A-Za-z0-9]*\.[A-Z][A-Z0-9_]*$")


def strip_enum_text(value: Any) -> Any:
    """``"Vendor.CISCO"`` becomes ``"CISCO"``, in strings, lists and mappings at any depth."""
    if isinstance(value, str):
        return value.split(".", 1)[1] if _ENUM_TEXT.match(value) else value
    if isinstance(value, list):
        return [strip_enum_text(item) for item in value]
    if isinstance(value, Mapping):
        return {key: strip_enum_text(item) for key, item in value.items()}
    return value


def decode_rows(
    rows: Iterable[Mapping[str, Any]],
    into: type[T],
    *,
    normalize_enums: bool = False,
) -> list[T]:
    """Validate each row into ``into`` (a pydantic model, dataclass or TypedDict).

    ``normalize_enums`` strips the ``Type.`` prefix from enum values first, so a
    model can declare ``vendor: Literal["CISCO", ...]`` rather than the dotted
    text. It goes by shape, so ordinary text shaped like an enum is rewritten
    too: a device named ``NYC.CORE1`` becomes ``CORE1``. A row that does not
    fit raises ``ValueError`` naming its index.
    """
    adapter = TypeAdapter(into)
    out: list[T] = []
    for index, row in enumerate(rows):
        data = strip_enum_text(dict(row)) if normalize_enums else row
        try:
            out.append(adapter.validate_python(data))
        except ValidationError as error:
            raise ValueError(f"NQE row {index} does not fit {into!r}: {error}") from None
    return out


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _index(
    rows: Iterable[Mapping[str, Any]], keys: Sequence[str], side: str
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        missing = [column for column in keys if column not in row]
        if missing:
            raise ValueError(f"a {side} row has no {missing[0]!r} column to key the diff on")
        key = _canonical([row[column] for column in keys])
        if key in out:
            shown = ", ".join(repr(row[column]) for column in keys)
            raise ValueError(
                f"the {side} rows repeat the key {shown}: the key columns are not unique"
            )
        out[key] = row
    return out


def diff_rows(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    key_columns: Sequence[str],
) -> list[NqeDiffEntry]:
    """Compare two result sets of the same query, matching rows on ``key_columns``.

    Returns ``ADDED`` (only after), ``DELETED`` (only before) and ``MODIFIED``
    (same key, different values) entries in key order, as Forward's NQE diff
    does; unchanged rows are left out. Values compare as data, so column order
    does not matter, but ``1`` and ``1.0`` differ (Go treats them as equal,
    at the cost of precision above 2**53). A key repeated within one side, or a row missing a key
    column, raises ``ValueError``: a diff keyed on a non-unique column would
    pair the wrong rows.
    """
    if isinstance(key_columns, str) or not key_columns:
        raise ValueError("an NQE row diff needs at least one key column (a list of names)")
    old = _index(before, key_columns, "before")
    new = _index(after, key_columns, "after")
    out: list[NqeDiffEntry] = []
    for key in sorted(old.keys() | new.keys()):
        was, now = old.get(key), new.get(key)
        if now is None:
            out.append(NqeDiffEntry(type="DELETED", before=dict(was or {})))
        elif was is None:
            out.append(NqeDiffEntry(type="ADDED", after=dict(now)))
        elif _canonical(was) != _canonical(now):
            out.append(NqeDiffEntry(type="MODIFIED", before=dict(was), after=dict(now)))
    return out
