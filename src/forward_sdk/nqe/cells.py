"""Unwrapping NQE's typed run-result cells to plain values.

The internal run result (an NQE check's rows) is a record of cells, not plain
values. :func:`flatten_cells` unwraps one row. It reads only mappings and lists,
so it needs no model and no outside package.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["flatten_cells"]


def flatten_cells(row: Mapping[str, Any]) -> dict[str, Any]:
    """Unwrap one row of Forward's typed run result to plain values.

    The internal run result (an NQE check's rows, ``nqe.check_result()``) is a
    record of cells: a scalar is ``{"value": x, "metadata": {...}}``, a
    collection is a list of cells, and a one-of value (an enum such as a
    device's OS) is ``{"type": "OS", "alternative": {"value": "IOS_XE"}}``.
    Each column becomes ``x``, a list of plain values, or ``"IOS_XE"``.
    Decoding is by position, so a column named ``value`` stays a column. An
    object in a cell that is none of these is read as a nested record of
    cells. Metadata is dropped. Seen live on 26.11 for scalars, lists and
    one-of values; nested records are not yet seen.
    """
    return {column: _cell(cell) for column, cell in row.items()}


def _cell(cell: Any) -> Any:
    if isinstance(cell, list):
        return [_cell(item) for item in cell]
    if isinstance(cell, Mapping):
        if "alternative" in cell and set(cell) <= {"type", "alternative"}:
            return _cell(cell["alternative"])
        if "value" in cell and set(cell) <= {"value", "metadata"}:
            return cell["value"]
        return flatten_cells(cell)
    return cell
