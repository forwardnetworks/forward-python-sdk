"""Network Query Engine support.

NQE is how most integrations read the Forward model: rather than fetching typed
resources, they run a query and consume rows. This package holds the parts of
that with no I/O -- how a query is referenced, how result pages are validated,
how query files are loaded and how predicates are built -- so they can be used
and tested on their own.
"""

from __future__ import annotations

from forward_sdk.nqe.bundle import NqeBundle, NqeBundleError, bundle_sources
from forward_sdk.nqe.pagination import PageGuards, PageTracker
from forward_sdk.nqe.query_ref import LATEST_PROCESSED, QueryRef, sanitize_commit_id
from forward_sdk.nqe.rows import decode_rows, diff_rows, flatten_cells, strip_enum_text
from forward_sdk.nqe.state import AclEntry, BgpSession, InterfaceAdmin, LldpAdjacency

__all__ = [
    "LATEST_PROCESSED",
    "AclEntry",
    "BgpSession",
    "InterfaceAdmin",
    "LldpAdjacency",
    "NqeBundle",
    "NqeBundleError",
    "PageGuards",
    "PageTracker",
    "QueryRef",
    "bundle_sources",
    "decode_rows",
    "diff_rows",
    "flatten_cells",
    "sanitize_commit_id",
    "strip_enum_text",
]
