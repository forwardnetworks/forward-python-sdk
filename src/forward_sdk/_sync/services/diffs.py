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
from collections.abc import Sequence
from dataclasses import dataclass

from forward_sdk._generated.models import (
    BidirectionalDiffCount,
    BidirectionalVulnerabilityDiff,
    ChecksDiff,
    DiffCount,
    FileDiffSummary,
    LocationConnChangeCounts,
    RoutingLoopDiffCount,
    SubnetConnectivityDiff,
    VulnerabilityDiffCounts,
)
from forward_sdk._ops import diffs as ops
from forward_sdk._ops import rest
from forward_sdk._sync.services._base import Service
from forward_sdk.errors import ForwardTimeoutError


@dataclass(frozen=True)
class LocationPairs:
    """:meth:`SnapshotDiffsService.wait_for_location_pairs`' answer."""

    #: The changed subnet pairs counted per location pair.
    pairs: LocationConnChangeCounts
    #: The settled subnet-connectivity comparison the pairs come from.
    comparison: SubnetConnectivityDiff

    @property
    def no_forwarding_change(self) -> bool:
        """Forward found no route, ACL or NAT change affecting any subnet.

        Forward compares only the subnet pairs such a change could affect
        (``LocalDiffComputer.generateDeviceSubnetPairs``), so zero pairs is a
        real "no reachability change", not missing data. The one degenerate
        case is a network with no device subnets at all. Seen live on 26.10.6:
        two snapshots with an empty route diff gave zero pairs, while six route
        changes gave 42.
        """
        return not self.comparison.total_subnet_pairs

    @property
    def nothing_compared(self) -> bool:
        """The same as :attr:`no_forwarding_change`, kept for 0.1.38 callers.

        Its 0.1.38 docstring said empty pairs then meant nothing; that was
        wrong. Zero pairs means no forwarding change.
        """
        return self.no_forwarding_change


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

        ``total_subnet_pairs`` counts only the subnet pairs a route, ACL or NAT
        change between the two snapshots could affect (Forward source,
        ``LocalDiffComputer.generateDeviceSubnetPairs``). Zero means Forward
        found no such change: no reachability change, not missing data (seen
        live on 26.10.6).

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

    def wait_for_location_pairs(
        self,
        before: str,
        after: str,
        type: str,
        *,
        loc: str | None = None,
        timeout: float = 300.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> LocationPairs:
        """Changed subnet pairs per location pair, once Forward has computed them.

        ``get_connectivity_diff_location_pairs`` answers ``{"counts": []}``
        while the comparison is still partial, which reads exactly like "no
        change". This waits for the comparison to settle
        (:meth:`wait_for_subnet_connectivity`) and then reads the pairs, and
        returns both. Empty ``pairs`` then means no change between located
        subnets; ``no_forwarding_change`` says Forward found no route, ACL or
        NAT change to compare at all. Pairs are grouped by
        the network's current device locations, so devices with no location
        are not counted. ``type`` is ``ADDED``, ``DELETED`` or ``MODIFIED``
        (Forward answers 400 to anything else, ``ALL`` included); ``loc``
        restricts to one location.

        Raises:
            ForwardTimeoutError: The comparison was still partial at the deadline.
        """
        kind = type.strip().upper()
        if kind not in ("ADDED", "DELETED", "MODIFIED"):
            raise ValueError(f"type must be ADDED, DELETED or MODIFIED, not {type!r}")
        comparison = self.wait_for_subnet_connectivity(
            before, after, timeout=timeout, poll_interval=poll_interval
        )
        payload = self._send_json(
            rest.BUILDERS["getConnectivityDiffLocationPairs"](
                before_snapshot_id=before, after_snapshot_id=after, type=kind, loc=loc
            )
        )
        return LocationPairs(
            pairs=LocationConnChangeCounts.model_validate(payload or {}), comparison=comparison
        )

    def wait_for_check_diffs(
        self,
        before: str,
        after: str,
        types: Sequence[str] | str,
        *,
        timeout: float = 600.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_poll_interval: float | None = None,
    ) -> ChecksDiff:
        """The check diff once no row is still ``PROCESSING``.

        Forward answers ``type=NQE`` at once with rows whose ``diffType`` (or
        either side's ``status``) is ``PROCESSING``; the other check types block
        in Forward until computed, so they return on the first read. With
        ``max_poll_interval`` above ``poll_interval`` the wait doubles after each
        read up to that ceiling, so a long wait does not poll fast throughout.

        The ``PROCESSING`` row shape is from Forward's source and the Go SDK
        v0.8.29, which records it as seen live by a regression sweep.

        Raises:
            ForwardTimeoutError: If a row is still processing at the deadline.
                The last result is on ``.partial``.
        """
        wanted = [types] if isinstance(types, str) else list(types)
        if not wanted:
            raise ValueError("wait_for_check_diffs needs at least one check type")
        deadline = time.monotonic() + timeout
        interval = poll_interval if poll_interval > 0 else DEFAULT_POLL_INTERVAL
        while True:
            payload = self._send_json(
                rest.BUILDERS["getCheckDiffs"](
                    before_snapshot_id=before, after_snapshot_id=after, type=wanted
                )
            )
            result = ChecksDiff.model_validate(payload or {})
            if not _check_diff_processing(result):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = ForwardTimeoutError(
                    f"check diff of {before} and {after} ({', '.join(wanted)}) was still "
                    f"processing after {timeout:.0f}s"
                )
                error.partial = result  # type: ignore[attr-defined]
                raise error
            time.sleep(min(interval, remaining))
            if max_poll_interval is not None and max_poll_interval > interval:
                interval = min(interval * 2, max_poll_interval)

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


def _check_diff_processing(result: ChecksDiff) -> bool:
    """Whether any row, or either side of one, is still being computed."""
    for row in result.checks or []:
        if _is_processing(row.diff_type):
            return True
        if any(side is not None and _is_processing(side.status) for side in (row.a, row.b)):
            return True
    return False


def _is_processing(value: object) -> bool:
    return str(getattr(value, "value", value) or "").upper() == "PROCESSING"
