# Generated from src/forward_sdk/_async/services/change_reports.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Comparing two snapshots as one ChangeReport: the reads, per the shared ``## Reads`` rules."""

from __future__ import annotations

import builtins
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from forward_sdk._version import __version__
from forward_sdk.change_report import (
    ChangeReport,
    ChangeReportInputError,
    Flow,
    _validate_section,
    assemble,
)
from forward_sdk.errors import (
    ForwardBadRequestError,
    ForwardError,
    ForwardNotFoundError,
    ForwardPermissionError,
    ForwardTimeoutError,
)

if TYPE_CHECKING:  # pragma: no cover
    from forward_sdk._sync.client import ForwardClient

__all__ = ["ChangeReportsService"]

#: The check types whose diffs are read, one request each.
CHECK_TYPES = ("Existential", "Isolation", "Reachability", "QueryStringBased", "Predefined", "NQE")
_FAILED_STATES = frozenset({"FAILED", "CANCELED", "TIMED_OUT", "RESTORE_FAILED"})
_GATING_DENIALS = frozenset({"license", "license_expired", "org_setting", "deployment_setting"})
_FLOW_BUCKETS = {
    "VALID": "valid",
    "DROPPED": "dropped",
    "BLACKHOLE": "blackhole",
    "UNREACHABLE": "unreachable",
    "LOOP": "loop",
}
_UNSUPPORTED_FIREWALL = "Device is not supported for firewall predict"


def _unavailable(error: ForwardError) -> dict[str, Any]:
    if isinstance(error, ForwardTimeoutError):
        kind = "timeout"
    elif isinstance(error, ForwardPermissionError):
        denial = error.denial
        kind = (
            "feature_gated"
            if denial is not None and denial.kind in _GATING_DENIALS
            else "forbidden"
        )
    elif isinstance(error, ForwardNotFoundError) and error.endpoint_not_served:
        kind = "not_served"
    else:
        kind = "error"
    return {"status": "unavailable", "reason": {"kind": kind, "message": str(error)}}


def _not_served(error: ForwardError) -> bool:
    return isinstance(error, ForwardNotFoundError) and error.endpoint_not_served


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


class ChangeReportsService:
    """``client.change_reports``: compare two snapshots, Predict or before/after.

    Built only from reads the SDK already makes; see the shared rules in
    ``tests/fixtures/change_report/RULES.md`` (``## Reads``). Mirrors the Go
    SDK's ``ChangeReports.Compare`` (v0.8.50). Preview: the reads were checked
    against Forward's source and recorded shapes; schema_version 1 is a draft
    until both SDKs ship it.
    """

    def __init__(self, client: ForwardClient) -> None:
        self._client = client

    def compare(
        self,
        base: str,
        after: str,
        *,
        network_id: str | None = None,
        change_set_id: str | None = None,
        after_kind: str | None = None,
        flows: Sequence[Flow] = (),
        state: bool = False,
        max_items: int = 100,
        section_timeout: float = 300.0,
        created_at: str | None = None,
    ) -> ChangeReport:
        """Compare snapshot ``after`` with ``base`` and return the report.

        ``after`` is a predicted snapshot (Predict) or a later collection;
        ``after_kind`` overrides what Forward says it is. Pass the change set
        for a prediction so its security rules can be read. ``flows`` and
        ``state=True`` add the opt-in sections. ``section_timeout`` is the budget
        for each section that waits (the checks, across all check types, and the
        connectivity comparison); a section
        that cannot be read is reported as unavailable with its reason, never
        as a partial answer, and makes the verdict ``incomplete`` when it is one
        that drives the verdict. Sections are read one after another.
        """
        # The caller's own input is checked before any read, and raised, never
        # reported as a section Forward answered badly.
        if after_kind not in (None, "predicted", "collected"):
            raise ValueError(f"after_kind must be predicted or collected, not {after_kind!r}")
        if max_items < 1:
            raise ValueError(f"max_items must be at least 1, not {max_items}")
        names = [flow.name for flow in flows]
        if len(set(names)) != len(names):
            raise ValueError("each flow needs its own name")
        for flow in flows:
            if flow.expectation not in ("valid", "none", "any"):
                raise ValueError(
                    f"flow {flow.name!r}: expectation must be valid, none or any, "
                    f"not {flow.expectation!r}"
                )
        network = self._client.networks._network(network_id)
        snapshot = self._client.snapshots._find(network, after)
        trigger = _text(snapshot.processing_trigger)
        kind = after_kind or ("predicted" if trigger == "PREDICT" else "collected")
        if kind not in ("predicted", "collected"):
            raise ValueError(f"after_kind must be predicted or collected, not {kind!r}")
        sections: dict[str, Any] = {}
        reads: list[tuple[str, Callable[[], Any]]] = [
            ("checks", lambda: self._checks(base, after, section_timeout)),
            ("connectivity", lambda: self._connectivity(base, after, section_timeout)),
            ("routing", lambda: self._routing(base, after)),
            ("config", lambda: self._config(base, after)),
            ("devices", lambda: self._devices(base, after)),
            ("diff_counts", lambda: self._diff_counts(base, after)),
            ("vulnerabilities", lambda: self._vulnerabilities(base, after)),
        ]
        if state:
            reads.append(("state", lambda: self._state(base, after, network)))
        if flows:
            reads.append(("flows", lambda: self._flows(base, after, network, flows)))
        if kind == "predicted":
            reads.append(("predict", lambda: self._predict(snapshot)))
            reads.append(("security_rules", lambda: self._security_rules(network, change_set_id)))
        for name, read in reads:
            try:
                section = read()
                _validate_section(name, section, max_items=max_items)
                sections[name] = section
            except ForwardError as error:
                sections[name] = _unavailable(error)
            except ChangeReportInputError as error:
                # Data the report's rules do not allow (a status a newer Forward
                # added, a repeated identity): this section only, never the report.
                sections[name] = {
                    "status": "unavailable",
                    "reason": {
                        "kind": "error",
                        "message": f"unexpected data from Forward: {error}",
                    },
                }

        data = {
            "meta": {
                "base_snapshot_id": base,
                "after_snapshot_id": after,
                "after_kind": kind,
                "change_set_id": change_set_id,
                "network_id": network,
            },
            "options": {
                "created_at": created_at
                or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "max_items": max_items,
                "sdk": {"name": "forward-sdk", "version": __version__},
                "expectations": {"state_expected_removed": []},
            },
            "sections": sections,
        }
        return ChangeReport(assemble(data))

    # -- sections ------------------------------------------------------------

    def _checks(self, base: str, after: str, timeout: float) -> dict[str, Any]:
        items: builtins.list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout  # one budget for the whole section
        for check_type in CHECK_TYPES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ForwardTimeoutError(
                    "the check diffs did not finish within the section budget"
                )
            try:
                diff = self._client.snapshot_diffs.wait_for_check_diffs(
                    base, after, [check_type], timeout=remaining
                )
            except ForwardNotFoundError as error:
                if _not_served(error):
                    continue  # this build does not diff that check type
                raise
            for row in diff.checks or []:
                side = row.b or row.a
                if side is None:
                    continue
                base_status = _text(row.a.status) if row.a is not None else None
                after_status = _text(row.b.status) if row.b is not None else None
                if (row.a is not None and base_status is None) or (
                    row.b is not None and after_status is None
                ):
                    raise ChangeReportInputError(f"check {side.id} has a side with no status")
                for status in (base_status, after_status):
                    if status == "PROCESSING":  # the wait ended without it finishing
                        raise ForwardTimeoutError(f"check {side.id} is still PROCESSING")
                items.append(
                    {
                        "check_id": str(side.id),
                        "name": side.name or "",
                        "type": check_type,
                        "base_status": base_status,
                        "after_status": after_status,
                    }
                )
        return {"status": "ok", "items": items}

    def _connectivity(self, base: str, after: str, timeout: float) -> dict[str, Any]:
        summary = self._client.snapshot_diffs.wait_for_subnet_connectivity(
            base, after, timeout=timeout
        )
        pairs: dict[str, builtins.list[dict[str, Any]]] = {}
        for kind in ("ADDED", "DELETED", "MODIFIED"):
            answer = self._client.snapshot_diff_details.get_connectivity_diff_location_pairs(
                before_snapshot_id=base, after_snapshot_id=after, type=kind
            )
            pairs[kind] = [
                {"src": p.src or "", "dst": p.dst or "", "count": p.count or 0}
                for p in answer.counts or []
            ]
        return {
            "status": "ok",
            "total_subnet_pairs": summary.total_subnet_pairs or 0,
            "evaluated_subnet_pairs": summary.evaluated_subnet_pairs or 0,
            "location_pairs": pairs,
        }

    def _routing(self, base: str, after: str) -> dict[str, Any]:
        stats = self._client.snapshot_diff_details.get_routing_diff_stats(
            before_snapshot_id=base, after_snapshot_id=after
        )
        items = []
        for device in stats.device_infos or []:
            counts = device.stats
            added = (counts.added if counts else 0) or 0
            removed = (counts.deleted if counts else 0) or 0
            modified = (counts.modified if counts else 0) or 0
            if added or removed or modified:
                items.append(
                    {
                        "device": device.device_name or device.name or "",
                        "added": added,
                        "removed": removed,
                        "modified": modified,
                    }
                )
        return {"status": "ok", "items": items}

    def _config(self, base: str, after: str) -> dict[str, Any]:
        diff = self._client.snapshot_diff_details.get_file_diffs(
            before_snapshot_id=base, after_snapshot_id=after, type="CONFIG"
        )
        items = []
        for device in diff.device_infos or []:
            name = device.device_name or device.name or ""
            files = []
            for stored in device.files or []:
                file_name = stored.name or ""
                prefix = f"{name},"
                files.append(
                    file_name[len(prefix) :] if file_name.startswith(prefix) else file_name
                )
            if files:
                items.append({"device": name, "files_changed": files})
        return {"status": "ok", "items": items}

    def _devices(self, base: str, after: str) -> dict[str, Any]:
        entries = self._client.snapshot_diff_details.get_device_diffs(
            before_snapshot_id=base, after_snapshot_id=after
        )
        parts: dict[str, builtins.list[str]] = {"added": [], "removed": [], "modified": []}
        bucket = {"ADDED": "added", "DELETED": "removed", "MODIFIED": "modified"}
        for entry in entries:
            target = bucket.get(_text(entry.diff_type) or "")
            side = entry.b or entry.a
            if target and side is not None and side.name:
                parts[target].append(side.name)
        return {"status": "ok", **parts}

    def _diff_counts(self, base: str, after: str) -> dict[str, Any]:
        details = self._client.snapshot_diff_details
        diffs = self._client.snapshot_diffs

        def count(area: str) -> Callable[[], Any]:
            return lambda: diffs.count(area, base, after)

        reads: dict[str, Callable[[], Any]] = {
            area: count(area) for area in ("devices", "interfaces", "topology", "l2", "acl")
        }
        reads["cloud_objects"] = lambda: details.get_cloud_object_diff_count(
            before_snapshot_id=base, after_snapshot_id=after
        )
        reads["cloud_acl"] = lambda: details.get_cloud_acl_diff_count(
            before_snapshot_id=base, after_snapshot_id=after
        )
        reads["routing_loop"] = lambda: diffs.routing_loops(base, after)
        families: dict[str, dict[str, Any]] = {}
        for family, read in reads.items():
            try:
                answer = read()
            except ForwardNotFoundError as error:
                if _not_served(error):
                    continue
                raise
            families[family] = {
                "count": answer.count or 0,
                "complete": answer.complete is not False,
            }
        return {"status": "ok", "families": families}

    def _vulnerabilities(self, base: str, after: str) -> dict[str, Any]:
        both = self._client.snapshot_diffs.vulnerabilities_bidirectional(base, after)
        warnings, improvements = both.warnings, both.improvements
        return {
            "status": "ok",
            "introduced": (warnings.cve_count if warnings else 0) or 0,
            "resolved": (improvements.cve_count if improvements else 0) or 0,
            "exposed_devices": warnings.exposed_vulnerable_devices_count if warnings else None,
        }

    def _state(self, base: str, after: str, network: str) -> dict[str, Any]:
        nqe = self._client.nqe

        def side(snapshot: str) -> builtins.list[dict[str, Any]]:
            rows: builtins.list[dict[str, Any]] = []
            for session in nqe.bgp_sessions(snapshot_id=snapshot, network_id=network):
                rows.append(
                    {
                        "kind": "bgp_session",
                        "identity": list(session.key),
                        "value": session.session_state,
                    }
                )
            for adjacency in nqe.lldp_adjacencies(snapshot_id=snapshot, network_id=network):
                rows.append(
                    {
                        "kind": "lldp_adjacency",
                        "identity": [str(part or "") for part in adjacency.key],
                        "value": "present",
                    }
                )
            for interface in nqe.interface_admin(snapshot_id=snapshot, network_id=network):
                rows.append(
                    {
                        "kind": "interface_admin",
                        "identity": list(interface.key),
                        "value": interface.admin_status,
                    }
                )
            counts = nqe.acl_counts(snapshot_id=snapshot, network_id=network)
            for (device, acl), count in counts.items():
                rows.append({"kind": "acl_entries", "identity": [device, acl], "value": count})
            return rows

        return {"status": "ok", "base": side(base), "after": side(after)}

    def _flows(self, base: str, after: str, network: str, flows: Sequence[Flow]) -> dict[str, Any]:
        items: builtins.list[dict[str, Any]] = []
        for flow in flows:
            buckets: builtins.list[dict[str, int] | None] = []
            for snapshot in (base, after):
                facets = self._client.flow_trace.get_trace_facets(
                    body=flow.query.request(), snapshot_id=snapshot, network_id=network
                )
                total = facets.total_results
                if facets.timed_out or (total is not None and _text(total.type) == "LOWER_BOUND"):
                    buckets.append(None)
                    continue
                counts = dict.fromkeys(
                    ("valid", "dropped", "blackhole", "unreachable", "loop", "other"), 0
                )
                groups = facets.facets.path_groups if facets.facets else None
                for group in groups or []:
                    bucket = _FLOW_BUCKETS.get((group.trace_type or "").upper(), "other")
                    counts[bucket] += len(group.paths or [])
                buckets.append(counts)
            if buckets[0] is None or buckets[1] is None:
                items.append(
                    {
                        "name": flow.name,
                        "expectation": flow.expectation,
                        "unavailable": {
                            "kind": "timeout",
                            "message": "the trace search did not finish "
                            "(timed out, or a lower-bound total)",
                        },
                    }
                )
            else:
                items.append(
                    {
                        "name": flow.name,
                        "expectation": flow.expectation,
                        "base": buckets[0],
                        "after": buckets[1],
                    }
                )
        return {"status": "ok", "items": items}

    def _predict(self, snapshot: Any) -> dict[str, Any]:
        state = _text(snapshot.state) or "UNKNOWN"
        failures = (
            [{"device": "-", "message": f"snapshot processing ended {state}"}]
            if state in _FAILED_STATES
            else []
        )
        return {
            "status": "ok",
            "processing_state": state,
            "failures": failures,
            "modeling_problems": [],
        }

    def _security_rules(self, network: str, change_set_id: str | None) -> dict[str, Any]:
        if not change_set_id:
            return {
                "status": "unavailable",
                "reason": {"kind": "error", "message": "change set not given"},
            }
        handle = self._client.change_sets.handle(change_set_id, network_id=network)
        draft = handle.draft()
        changes: Mapping[str, Any] | None = draft.device_to_changes if draft is not None else None
        if not changes:
            changes = (handle.head()).device_to_changes
        items: builtins.list[dict[str, Any]] = []
        change_name = {"ADDED": "added", "DELETED": "removed", "MODIFIED": "modified"}
        for device in sorted(changes or {}):
            try:
                diff = self._client.firewall_predict.get_security_rules_diff(
                    change_set_id=change_set_id, device_name=device, network_id=network
                )
            except ForwardBadRequestError as error:
                if _UNSUPPORTED_FIREWALL in str(error):
                    continue  # not a firewall Forward's predict supports: no rows
                raise
            for entry in diff.entries or []:
                change = change_name.get(_text(entry.diff_type) or "")
                rule = entry.b or entry.a
                if change is None or rule is None:
                    continue
                name = rule.definition.name if rule.definition is not None else ""
                items.append(
                    {
                        "device": device,
                        "rule": f"{rule.scope_id or ''}/{rule.rulebase_id or ''}/{name or ''}",
                        "change": change,
                    }
                )
        return {"status": "ok", "items": items}
