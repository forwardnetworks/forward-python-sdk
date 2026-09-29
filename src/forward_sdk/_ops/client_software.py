"""Request builders for Forward client packages.

Unpublished: described by hand in ``spec/unpublished.yaml``. The response is a
binary archive, so the builder streams it and accepts any media type -- fwd.app
serves the headless collector as ``application/gzip``.
"""

from __future__ import annotations

from forward_sdk._ops import RequestSpec, op, spec_for


@op("getClientPackage")
def get_client_package(*, type: str) -> RequestSpec:
    """Download the client package of ``type`` (for example ``HEADLESS_LINUX``)."""
    return spec_for("getClientPackage", query={"type": type}, accept="*/*", stream=True)
