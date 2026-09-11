"""Request builders for snapshot comparisons.

These endpoints are unpublished: absent from Forward's generated description,
so their shapes are written out in ``spec/unpublished.yaml``. They exist to say
what a change did to a network without the caller having to know which question
to ask, which is what makes them worth the hand-written description.
"""

from __future__ import annotations

from forward_sdk._ops import RequestSpec, op, spec_for


def _pair(before: str, after: str) -> dict[str, str]:
    return {"beforeSnapshotId": before, "afterSnapshotId": after}


@op("getSubnetConnectivityDiff")
def subnet_connectivity(*, before: str, after: str) -> RequestSpec:
    return spec_for("getSubnetConnectivityDiff", path_params=_pair(before, after))


@op("getVulnerabilityDiffCounts")
def vulnerability_counts(*, before: str, after: str) -> RequestSpec:
    return spec_for("getVulnerabilityDiffCounts", path_params=_pair(before, after))


@op("getRoutingLoopDiffCount")
def routing_loops(*, before: str, after: str) -> RequestSpec:
    return spec_for("getRoutingLoopDiffCount", path_params=_pair(before, after))


# One shape serves every per-area count.
_COUNTS = {
    "devices": "getDeviceDiffCount",
    "interfaces": "getInterfaceDiffCount",
    "topology": "getTopologyDiffCount",
    "l2": "getL2DiffCount",
    "acl": "getAclDiffCount",
    "nat": "getNatDiffCount",
    "arp": "getArpDiffCount",
    "mac": "getMacDiffCount",
}


def _count_builder(operation_id: str):  # type: ignore[no-untyped-def]
    @op(operation_id)
    def build(*, before: str, after: str) -> RequestSpec:
        return spec_for(operation_id, path_params=_pair(before, after))

    return build


COUNT_BUILDERS = {area: _count_builder(operation_id) for area, operation_id in _COUNTS.items()}


@op("getRoutingLoopDiffBidirectional")
def routing_loops_bidirectional(*, before: str, after: str) -> RequestSpec:
    return spec_for("getRoutingLoopDiffBidirectional", path_params=_pair(before, after))


@op("getVulnerabilityDiffBidirectional")
def vulnerabilities_bidirectional(*, before: str, after: str) -> RequestSpec:
    return spec_for("getVulnerabilityDiffBidirectional", path_params=_pair(before, after))


@op("getFileDiffSummary")
def file_summary(*, before: str, after: str) -> RequestSpec:
    return spec_for("getFileDiffSummary", path_params=_pair(before, after))
