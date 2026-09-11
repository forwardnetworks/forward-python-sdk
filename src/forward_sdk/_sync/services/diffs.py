# Generated from src/forward_sdk/_async/services/diffs.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Comparing two snapshots.

These endpoints are unpublished: absent from Forward's public API description,
so the SDK describes their shapes by hand. They are the most useful evidence a
change rehearsal produces, because they state blast radius without being asked
a question: how many site pairs lost connectivity, how many CVEs became
reachable, whether a routing loop appeared.
"""

from __future__ import annotations

import time

from forward_sdk._generated.models import (
    BidirectionalDiffCount,
    BidirectionalVulnerabilityDiff,
    DiffCount,
    FileDiffSummary,
    RoutingLoopDiffCount,
    SubnetConnectivityDiff,
    VulnerabilityDiffCounts,
)
from forward_sdk._ops import diffs as ops
from forward_sdk._sync.services._base import Service
from forward_sdk.errors import ForwardTimeoutError

#: How often to re-read a connectivity comparison that is still computing.
DEFAULT_POLL_INTERVAL = 2.0


class SnapshotDiffsService(Service):
    """Differences between two snapshots, typically a baseline and a prediction."""

    def subnet_connectivity(self, before: str, after: str) -> SubnetConnectivityDiff:
        """How connectivity between subnets changed, as Forward has it right now.

        Computed asynchronously. The first read after a prediction finishes
        very often lands inside the window before computation starts, and comes
        back with every count at zero and ``is_partial_result`` true. Those
        zeros mean "not finished", and they are indistinguishable from "nothing
        changed" unless you check the flag. :meth:`wait_for_subnet_connectivity`
        does that for you.
        """
        payload = self._send_json(ops.subnet_connectivity(before=before, after=after))
        return SubnetConnectivityDiff.model_validate(payload or {})

    def wait_for_subnet_connectivity(
        self,
        before: str,
        after: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> SubnetConnectivityDiff:
        """The connectivity comparison once Forward has finished computing it.

        Polls until ``is_partial_result`` is false. A snapshot-ready webhook
        fires the instant a prediction finishes, so a caller reacting to one
        arrives before this computation has started almost every time; reading
        once there reports zero change on a change that isolated seven sites.

        A settled result of zero is still ambiguous, and the two readings are
        opposites. Either the change altered no site-to-site connectivity, or the
        network has no locations defined and the comparison had nothing to
        compute over. ``total_subnet_pairs`` separates them: zero pairs means
        nothing was compared, not that nothing changed.

        Raises:
            ForwardTimeoutError: If the comparison is still partial at the
                deadline. The last partial result is on ``.partial``.
        """
        deadline = time.monotonic() + timeout
        while True:
            result = self.subnet_connectivity(before, after)
            if not result.is_partial_result:
                return result
            if time.monotonic() >= deadline:
                error = ForwardTimeoutError(
                    f"subnet connectivity comparison of {before} and {after} was still "
                    f"partial after {timeout:.0f}s ({result.evaluated_subnet_pairs or 0} of "
                    f"{result.total_subnet_pairs or 0} pairs evaluated)"
                )
                error.partial = result  # type: ignore[attr-defined]
                raise error
            time.sleep(poll_interval)

    def vulnerability_counts(self, before: str, after: str) -> VulnerabilityDiffCounts:
        """Vulnerability exposure the change introduces.

        Only ``new_cve_count`` is present on every deployment. The per-device
        count appears where vulnerability analysis is licensed.
        """
        payload = self._send_json(ops.vulnerability_counts(before=before, after=after))
        return VulnerabilityDiffCounts.model_validate(payload or {})

    def routing_loops(self, before: str, after: str) -> RoutingLoopDiffCount:
        """How many routing loops the change introduces.

        ``complete`` false means the search stopped early and ``count`` is a
        lower bound, so a zero there does not clear the change.
        """
        payload = self._send_json(ops.routing_loops(before=before, after=after))
        return RoutingLoopDiffCount.model_validate(payload or {})

    def routing_loops_bidirectional(self, before: str, after: str) -> BidirectionalDiffCount:
        """Loops the change introduces and loops it resolves, separately.

        Needs the PREDICT_DIFF_INSIGHT_ENHANCEMENTS org property.
        """
        payload = self._send_json(ops.routing_loops_bidirectional(before=before, after=after))
        return BidirectionalDiffCount.model_validate(payload or {})

    def vulnerabilities_bidirectional(
        self, before: str, after: str
    ) -> BidirectionalVulnerabilityDiff:
        """Exposure the change introduces and exposure it resolves, separately.

        Needs the PREDICT_DIFF_INSIGHT_ENHANCEMENTS org property. The field
        names differ from :meth:`vulnerability_counts`: ``cve_count`` rather
        than ``new_cve_count``.
        """
        payload = self._send_json(ops.vulnerabilities_bidirectional(before=before, after=after))
        return BidirectionalVulnerabilityDiff.model_validate(payload or {})

    def file_summary(self, before: str, after: str) -> FileDiffSummary:
        """How many devices and locations the change touches, directly and indirectly."""
        payload = self._send_json(ops.file_summary(before=before, after=after))
        return FileDiffSummary.model_validate(payload or {})

    def count(self, area: str, before: str, after: str) -> DiffCount:
        """How much changed in one area: devices, interfaces, topology, l2, acl, nat, arp or mac.

        ``complete`` false means the count is a lower bound so far.
        """
        try:
            builder = ops.COUNT_BUILDERS[area]
        except KeyError:
            raise ValueError(
                f"unknown diff area {area!r}; one of {', '.join(sorted(ops.COUNT_BUILDERS))}"
            ) from None
        payload = self._send_json(builder(before=before, after=after))
        return DiffCount.model_validate(payload or {})

    def counts(self, before: str, after: str) -> dict[str, DiffCount]:
        """Every per-area count at once, keyed by area.

        Eight requests. The one-line answer to "what did this change touch".
        """
        return {area: self.count(area, before, after) for area in ops.COUNT_BUILDERS}
