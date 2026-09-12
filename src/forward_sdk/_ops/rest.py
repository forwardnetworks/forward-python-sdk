"""Builders for the rest of Forward's published API.

These operations have ordinary shapes -- path template, declared query
parameters, optional JSON body -- so they are derived from the operation table
by :mod:`forward_sdk._ops._generic` rather than written out one by one. The
signature of each comes from the spec, so it cannot disagree with it.

The handful with unusual shapes are written explicitly at the end.
"""

from __future__ import annotations

from forward_sdk._generated.operations import OPERATIONS

# Import the hand-written builders first. This module claims whatever is left,
# so without a fixed order it would claim operations they own, and which module
# happened to be imported first would decide the SDK's behaviour.
from forward_sdk._ops import (  # noqa: F401
    REGISTRY,
    ai,
    change_sets,
    core,
    diffs,
    events,
    nqe,
    nqe_repo,
    tapi,
)
from forward_sdk._ops._generic import build_all

#: POST operations that only read. Forward uses POST where a query is too large
#: for a URL, and retrying one of these is as safe as retrying a GET.
READ_ONLY_POSTS = {
    "getPathsBulk",
    "getSpecificClassicDevices",
}

#: Whether a response is streamed, and what to accept, come from the operation
#: table. Only what cannot be derived is listed here.
SPECIAL: dict[str, dict[str, object]] = {
    # RFC 7464 record-separated JSON, streamed as results are found. Reading is
    # safe to retry despite the POST.
    "getPathsBulkSeq": {"idempotent": True},
}


def _remaining() -> list[str]:
    """Operations with no hand-written builder yet."""
    return sorted(set(OPERATIONS) - set(REGISTRY))


def _overrides(operation_ids: list[str]) -> dict[str, dict[str, object]]:
    settings: dict[str, dict[str, object]] = {}
    for operation_id in operation_ids:
        if operation_id in SPECIAL:
            settings[operation_id] = dict(SPECIAL[operation_id])
        elif operation_id in READ_ONLY_POSTS:
            settings[operation_id] = {"idempotent": True}
    return settings


_operation_ids = _remaining()
BUILDERS = build_all(_operation_ids, overrides=_overrides(_operation_ids))

# Expose each builder as a module attribute, so it can be imported by its
# Python name as well as looked up by operationId.
globals().update({builder.__name__: builder for builder in BUILDERS.values()})

__all__ = ["BUILDERS"]
