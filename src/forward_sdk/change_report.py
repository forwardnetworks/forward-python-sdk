"""A comparison of two snapshots as one report: the pure assemble and render steps.

A ``ChangeReport`` compares a base snapshot with a later one (a Predict
prediction, or a later collection) from reads the SDK already has: checks,
connectivity, routing, config files, devices, security rules, vulnerabilities,
diff counts and, when asked, flows and state. :func:`assemble` turns the
already-read section data into the report (schema_version ``"1"``) and
:func:`render_markdown` renders it, for a pull request comment, say.

The rules are being agreed with the Go SDK, which builds the same report, and
are pinned by golden files (``tests/fixtures/change_report``: ``RULES.md``,
``schema.json`` and the cases) that both SDKs test against byte for byte.
schema_version ``"1"`` is a DRAFT until both have shipped it. A report is built
from Forward's API; it is not the Predict UI's report.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from forward_sdk.trace import PathQuery

__all__ = [
    "SCHEMA_VERSION",
    "ChangeReport",
    "ChangeReportInputError",
    "Flow",
    "assemble",
    "render_markdown",
    "to_json",
]

SCHEMA_VERSION = "1"

#: Sections in report order, with their Markdown titles.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("checks", "Checks"),
    ("flows", "Flows"),
    ("state", "State"),
    ("connectivity", "Connectivity"),
    ("routing", "Routing"),
    ("config", "Config"),
    ("devices", "Devices"),
    ("security_rules", "Security rules"),
    ("vulnerabilities", "Vulnerabilities"),
    ("diff_counts", "Diff counts"),
    ("predict", "Predict"),
    ("ai_summary", "AI summary"),
)
_OPT_IN = frozenset({"flows", "state", "ai_summary"})
_PREDICTED_ONLY = frozenset({"predict", "security_rules"})
#: Statuses assemble accepts: every CheckStatus except PROCESSING, which is still computing.
_TERMINAL = frozenset(
    {
        "PASS",
        "FAIL",
        "ERROR",
        "TIMEOUT",
        "UNLICENSED",
        "SKIPPED",
        "NONE",
        "REQUIRES_ADDITIONAL_SNAPSHOT_PROCESSING",
    }
)
#: After statuses that mean the check could not be evaluated on the after snapshot.
_NOT_EVALUATED = frozenset({"TIMEOUT", "NONE", "REQUIRES_ADDITIONAL_SNAPSHOT_PROCESSING"})
_FAILED = frozenset({"FAIL", "ERROR"})
_PAIR_TYPES = ("ADDED", "DELETED", "MODIFIED")


class ChangeReportInputError(ValueError):
    """The input to :func:`assemble` breaks a rule the reading side must keep."""


_CHECK_RANK = {
    c: i for i, c in enumerate(("regressed", "other", "removed", "added", "fixed", "unchanged"))
}
_FLOW_RANK = {
    c: i
    for i, c in enumerate(
        ("lost_valid", "unavailable", "gained_valid", "counts_changed", "unchanged")
    )
}
_STATE_RANK = {c: i for i, c in enumerate(("lost", "changed", "gained"))}
_EXPECTATIONS = frozenset({"valid", "none", "any"})


def _no_duplicates(keys: Sequence[Any], what: str) -> None:
    seen: set[Any] = set()
    for key in keys:
        if key in seen:
            raise ChangeReportInputError(f"duplicate {what}: {key!r}")
        seen.add(key)


# -- assemble -----------------------------------------------------------------


def assemble(data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the report from already-read section data (see ``RULES.md``)."""
    meta_in = data["meta"]
    options = data.get("options") or {}
    sections_in: Mapping[str, Any] = data.get("sections") or {}
    cap = int(options.get("max_items", 100))
    if cap < 1:
        raise ChangeReportInputError(f"max_items must be at least 1, not {cap}")
    predicted = meta_in["after_kind"] == "predicted"
    expected = set((options.get("expectations") or {}).get("state_expected_removed") or [])
    reasons: list[dict[str, str]] = []
    incomplete = False

    sections: dict[str, Any] = {}
    for name, _title in SECTIONS:
        raw = sections_in.get(name)
        if raw is not None and name in _PREDICTED_ONLY and not predicted:
            raise ChangeReportInputError(f"section {name!r} applies only to a predicted snapshot")
        if raw is None:
            if name in _PREDICTED_ONLY and not predicted:
                sections[name] = {"status": "not_applicable"}
            elif name in _OPT_IN:
                sections[name] = {"status": "not_requested"}
            else:
                raise ChangeReportInputError(f"section {name!r} is missing from the input")
            continue
        if raw.get("status") not in ("ok", "unavailable"):
            raise ChangeReportInputError(f"section {name!r} has status {raw.get('status')!r}")
        if raw.get("status") == "unavailable":
            sections[name] = {"status": "unavailable", "reason": dict(raw["reason"])}
            if name in ("checks", "flows", "state"):
                incomplete = True
                reasons.append(_reason("section_unavailable", name, raw["reason"]["kind"]))
            continue
        builder = _BUILDERS[name]
        section, section_reasons, section_incomplete = builder(raw, cap, expected)
        sections[name] = section
        reasons.extend(section_reasons)
        incomplete = incomplete or section_incomplete

    reasons.sort(key=lambda r: (r["section"], r["code"], r["detail"]))
    if incomplete:
        result = "incomplete"
    elif any(
        r["code"] in ("check_regressed", "flow_lost_valid_path", "state_lost") for r in reasons
    ):
        result = "regression"
    else:
        result = "no_regression"

    meta = {
        "schema_version": SCHEMA_VERSION,
        "base_snapshot_id": meta_in["base_snapshot_id"],
        "after_snapshot_id": meta_in["after_snapshot_id"],
        "after_kind": meta_in["after_kind"],
        "change_set_id": meta_in.get("change_set_id"),
        "network_id": meta_in["network_id"],
        "created_at": options["created_at"],
        "sdk": dict(options["sdk"]),
    }
    return {"meta": meta, "verdict": {"result": result, "reasons": reasons}, "sections": sections}


def _reason(code: str, section: str, detail: str) -> dict[str, str]:
    return {"code": code, "section": section, "detail": detail}


def _capped(items: list[Any], cap: int) -> tuple[list[Any], bool]:
    return items[:cap], len(items) > cap


def _checks(raw: Mapping[str, Any], cap: int, _expected: set[str]) -> tuple[Any, list[Any], bool]:
    _no_duplicates([r["check_id"] for r in raw["items"]], "check_id")
    rows = sorted(raw["items"], key=lambda r: r["check_id"])
    counts = dict.fromkeys(
        (
            "regressed",
            "fixed",
            "added",
            "removed",
            "unchanged",
            "other",
            "not_evaluated",
            "skipped",
            "unlicensed",
        ),
        0,
    )
    reasons: list[dict[str, str]] = []
    incomplete = False
    items: list[dict[str, Any]] = []
    for row in rows:
        base, after = row.get("base_status"), row.get("after_status")
        for status in (base, after):
            if status is not None and status not in _TERMINAL:
                raise ChangeReportInputError(
                    f"check {row['check_id']}: {status} is still being computed; "
                    "the reader must not pass it"
                )
        if base is None and after is None:
            raise ChangeReportInputError(
                f"check {row['check_id']} has neither a base nor an after status"
            )
        if base is None:
            change = "added"
        elif after is None:
            change = "removed"
        elif base == after:
            change = "unchanged"
        elif base == "PASS" and after in _FAILED:
            change = "regressed"
            reasons.append(_reason("check_regressed", "checks", row["check_id"]))
        elif base in _FAILED and after == "PASS":
            change = "fixed"
        else:
            change = "other"
        if base == "PASS" and after in _NOT_EVALUATED:
            reasons.append(_reason("check_not_evaluated", "checks", row["check_id"]))
            incomplete = True
        counts[change] += 1
        if after in _NOT_EVALUATED:
            counts["not_evaluated"] += 1
        elif after == "SKIPPED":
            counts["skipped"] += 1
        elif after == "UNLICENSED":
            counts["unlicensed"] += 1
        items.append(
            {
                **{k: row.get(k) for k in ("check_id", "name", "type")},
                "base_status": base,
                "after_status": after,
                "change": change,
            }
        )
    items.sort(key=lambda i: (_CHECK_RANK[i["change"]], i["check_id"]))
    kept, truncated = _capped(items, cap)
    section = {
        "status": "ok",
        "items": kept,
        "counts": counts,
        "truncated": truncated,
        "total": len(items),
    }
    return section, reasons, incomplete


def _flows(raw: Mapping[str, Any], cap: int, _expected: set[str]) -> tuple[Any, list[Any], bool]:
    reasons: list[dict[str, str]] = []
    incomplete = False
    items: list[dict[str, Any]] = []
    _no_duplicates([r["name"] for r in raw["items"]], "flow name")
    for row in sorted(raw["items"], key=lambda r: r["name"]):
        expectation = row.get("expectation")
        if expectation not in _EXPECTATIONS:
            raise ChangeReportInputError(
                f"flow {row['name']!r}: expectation must be valid, none or any, not {expectation!r}"
            )
        if row.get("unavailable") is not None:
            items.append(
                {
                    "name": row["name"],
                    "expectation": expectation,
                    "base": None,
                    "after": None,
                    "change": "unavailable",
                    "unavailable": dict(row["unavailable"]),
                }
            )
            reasons.append(_reason("flow_unavailable", "flows", row["name"]))
            incomplete = True
            continue
        base, after = dict(row["base"]), dict(row["after"])
        if base["valid"] > 0 and after["valid"] == 0:
            change = "lost_valid"
            if expectation == "valid":
                reasons.append(_reason("flow_lost_valid_path", "flows", row["name"]))
        elif base["valid"] == 0 and after["valid"] > 0:
            change = "gained_valid"
        elif base != after:
            change = "counts_changed"
        else:
            change = "unchanged"
        items.append(
            {
                "name": row["name"],
                "expectation": expectation,
                "base": base,
                "after": after,
                "change": change,
                "unavailable": None,
            }
        )
    items.sort(key=lambda i: (_FLOW_RANK[i["change"]], i["name"]))
    kept, truncated = _capped(items, cap)
    return (
        {"status": "ok", "items": kept, "truncated": truncated, "total": len(items)},
        reasons,
        incomplete,
    )


def _identity_key(kind: str, identity: Sequence[str]) -> str:
    return f"{kind}:{'|'.join(identity)}"


def _lost(kind: str, base: Any, after: Any) -> bool:
    if kind == "bgp_session":
        return bool(base == "ESTABLISHED" and after != "ESTABLISHED")
    if kind == "lldp_adjacency":
        return after is None
    if kind == "interface_admin":
        return bool(base == "AdminStatus.UP" and after != "AdminStatus.UP")
    return False


def _state(raw: Mapping[str, Any], cap: int, expected: set[str]) -> tuple[Any, list[Any], bool]:
    def index(
        rows: Sequence[Mapping[str, Any]], side: str
    ) -> dict[tuple[str, tuple[str, ...]], Any]:
        _no_duplicates([(r["kind"], tuple(r["identity"])) for r in rows], f"{side} state row")
        return {(r["kind"], tuple(r["identity"])): r.get("value") for r in rows}

    base, after = index(raw["base"], "base"), index(raw["after"], "after")
    reasons: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    for key in sorted(base.keys() | after.keys()):
        kind, identity = key
        before, now = base.get(key), after.get(key)
        in_base, in_after = key in base, key in after
        if in_base and _lost(kind, before, now if in_after else None):
            change = "lost"
        elif not in_base:
            change = "gained"
        elif (in_after and before != now) or not in_after:
            change = "changed"
        else:
            continue
        label = _identity_key(kind, identity)
        is_expected = label in expected
        if change == "lost" and not is_expected:
            reasons.append(_reason("state_lost", "state", label))
        items.append(
            {
                "kind": kind,
                "identity": list(identity),
                "base": before if in_base else None,
                "after": now if in_after else None,
                "change": change,
                "expected": is_expected,
            }
        )
    items.sort(key=lambda i: (_STATE_RANK[i["change"]], i["kind"], tuple(i["identity"])))
    kept, truncated = _capped(items, cap)
    return (
        {"status": "ok", "items": kept, "truncated": truncated, "total": len(items)},
        reasons,
        False,
    )


def _connectivity(
    raw: Mapping[str, Any], cap: int, _expected: set[str]
) -> tuple[Any, list[Any], bool]:
    pairs_in = raw.get("location_pairs") or {}
    pairs: dict[str, list[Any]] = {}
    total = 0
    truncated = False
    for kind in _PAIR_TYPES:
        _no_duplicates(
            [(p["src"], p["dst"]) for p in pairs_in.get(kind) or []], f"{kind} location pair"
        )
        rows = sorted(
            (dict(p) for p in pairs_in.get(kind) or []), key=lambda p: (p["src"], p["dst"])
        )
        total += len(rows)
        pairs[kind], cut = _capped(rows, cap)
        truncated = truncated or cut
    section = {
        "status": "ok",
        "no_forwarding_change": raw["total_subnet_pairs"] == 0,
        "total_subnet_pairs": raw["total_subnet_pairs"],
        "evaluated_subnet_pairs": raw["evaluated_subnet_pairs"],
        "location_pairs": pairs,
        "truncated": truncated,
        "total": total,
    }
    return section, [], False


def _by_key(key: str) -> Any:
    def build(raw: Mapping[str, Any], cap: int, _expected: set[str]) -> tuple[Any, list[Any], bool]:
        _no_duplicates([tuple(i[k] for k in key.split(",")) for i in raw["items"]], key)
        items = sorted(
            (dict(i) for i in raw["items"]), key=lambda i: tuple(i[k] for k in key.split(","))
        )
        kept, truncated = _capped(items, cap)
        return (
            {"status": "ok", "items": kept, "truncated": truncated, "total": len(items)},
            [],
            False,
        )

    return build


def _config(raw: Mapping[str, Any], cap: int, _expected: set[str]) -> tuple[Any, list[Any], bool]:
    _no_duplicates([i["device"] for i in raw["items"]], "config device")
    items = sorted(
        (
            {"device": i["device"], "files_changed": sorted(i.get("files_changed") or [])}
            for i in raw["items"]
        ),
        key=lambda i: i["device"],
    )
    kept, truncated = _capped(items, cap)
    return {"status": "ok", "items": kept, "truncated": truncated, "total": len(items)}, [], False


def _devices(raw: Mapping[str, Any], cap: int, _expected: set[str]) -> tuple[Any, list[Any], bool]:
    section: dict[str, Any] = {"status": "ok"}
    total = 0
    truncated = False
    for part in ("added", "removed", "modified"):
        _no_duplicates(list(raw.get(part) or []), f"{part} device")
        names = sorted(raw.get(part) or [])
        total += len(names)
        section[part], cut = _capped(names, cap)
        truncated = truncated or cut
    section.update(truncated=truncated, total=total)
    return section, [], False


def _verbatim(*fields: str) -> Any:
    def build(
        raw: Mapping[str, Any], _cap: int, _expected: set[str]
    ) -> tuple[Any, list[Any], bool]:
        return {"status": "ok", **{f: copy.deepcopy(raw.get(f)) for f in fields}}, [], False

    return build


_BUILDERS: dict[str, Any] = {
    "checks": _checks,
    "flows": _flows,
    "state": _state,
    "connectivity": _connectivity,
    "routing": _by_key("device"),
    "config": _config,
    "devices": _devices,
    "security_rules": _by_key("device,rule"),
    "vulnerabilities": _verbatim("introduced", "resolved", "exposed_devices"),
    "diff_counts": _verbatim("families"),
    "predict": _verbatim("processing_state", "failures", "modeling_problems"),
    "ai_summary": lambda raw, _cap, _expected: _ai_summary(raw),
}


def _ai_summary(raw: Mapping[str, Any]) -> tuple[Any, list[Any], bool]:
    text = raw.get("text")
    if not isinstance(text, str) or not text or text.endswith("\n"):
        raise ChangeReportInputError(
            "ai_summary.text must be non-empty and must not end with a newline"
        )
    return {"status": "ok", "text": text}, [], False


def _validate_section(name: str, raw: Mapping[str, Any], *, max_items: int = 100) -> None:
    """Run one ``ok`` section through its builder, so a rule it breaks raises here.

    For a reader that wants to report bad data in one section (a status a newer
    Forward added, a repeated identity) as that section being unavailable,
    rather than have the whole report fail.

    Raises:
        ChangeReportInputError: The section breaks a rule.
    """
    if raw.get("status") != "ok" or name not in _BUILDERS:
        return
    _BUILDERS[name](raw, max_items, set())


def to_json(report: Mapping[str, Any]) -> str:
    """The report as the shared JSON text: sorted keys, two-space indent, one final newline."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# -- render -------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return str(value).replace("|", "\\|")


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _rank(order: Sequence[str]) -> dict[str, int]:
    return {name: i for i, name in enumerate(order)}


def _render_checks(s: Mapping[str, Any]) -> list[str]:
    changed = [i for i in s["items"] if i["change"] != "unchanged"]
    blocks = [
        _table(
            ["Check", "Name", "Type", "Base", "After", "Change"],
            [
                [
                    i["check_id"],
                    i["name"],
                    i["type"],
                    i["base_status"],
                    i["after_status"],
                    i["change"],
                ]
                for i in changed
            ],
        )
        if changed
        else "No changes."
    ]
    c = s["counts"]
    blocks.append(
        f"Regressed {c['regressed']}, fixed {c['fixed']}, added {c['added']}, "
        f"removed {c['removed']}, "
        f"unchanged {c['unchanged']}, other {c['other']}; not evaluated {c['not_evaluated']}, "
        f"skipped {c['skipped']}, unlicensed {c['unlicensed']}."
    )
    return blocks


def _render_flows(s: Mapping[str, Any]) -> list[str]:
    if not s["items"]:
        return ["No changes."]
    rows = s["items"]
    return [
        _table(
            ["Flow", "Expectation", "Base valid", "After valid", "Change"],
            [
                [
                    i["name"],
                    i["expectation"],
                    i["base"]["valid"] if i["base"] else None,
                    i["after"]["valid"] if i["after"] else None,
                    i["change"],
                ]
                for i in rows
            ],
        )
    ]


def _render_state(s: Mapping[str, Any]) -> list[str]:
    if not s["items"]:
        return ["No changes."]
    rows = s["items"]
    return [
        _table(
            ["Kind", "Identity", "Base", "After", "Change", "Expected"],
            [
                [
                    i["kind"],
                    ", ".join(i["identity"]),
                    i["base"],
                    i["after"],
                    i["change"],
                    "yes" if i["expected"] else "no",
                ]
                for i in rows
            ],
        )
    ]


def _render_connectivity(s: Mapping[str, Any]) -> list[str]:
    head = (
        "No forwarding change."
        if s["no_forwarding_change"]
        else f"Subnet pairs evaluated: {s['evaluated_subnet_pairs']} of {s['total_subnet_pairs']}."
    )
    rows = [
        [kind, p["src"], p["dst"], p["count"]]
        for kind in ("DELETED", "MODIFIED", "ADDED")
        for p in s["location_pairs"][kind]
    ]
    body = (
        _table(["Change", "From", "To", "Subnet pairs"], rows)
        if rows
        else "No location pair changes."
    )
    return [head, body]


def _render_rows(header: Sequence[str], fields: Sequence[str]) -> Any:
    def render(s: Mapping[str, Any]) -> list[str]:
        if not s["items"]:
            return ["No changes."]
        return [_table(header, [[_join(i[f]) for f in fields] for i in s["items"]])]

    return render


def _join(value: Any) -> Any:
    return ", ".join(value) if isinstance(value, list) else value


def _render_devices(s: Mapping[str, Any]) -> list[str]:
    def names(part: str) -> str:
        return ", ".join(s[part]) if s[part] else "none"

    return [
        f"Added: {names('added')}.\nRemoved: {names('removed')}.\nModified: {names('modified')}."
    ]


def _render_vulnerabilities(s: Mapping[str, Any]) -> list[str]:
    exposed = "-" if s["exposed_devices"] is None else s["exposed_devices"]
    return [f"Introduced {s['introduced']}, resolved {s['resolved']}, exposed devices {exposed}."]


def _render_diff_counts(s: Mapping[str, Any]) -> list[str]:
    families = s["families"] or {}
    if not families:
        return ["No changes."]
    return [
        _table(
            ["Family", "Count", "Complete"],
            [
                [name, f["count"], "yes" if f["complete"] else "no (lower bound)"]
                for name, f in sorted(families.items())
            ],
        )
    ]


def _render_predict(s: Mapping[str, Any]) -> list[str]:
    blocks = [f"Processing: {s['processing_state']}."]
    for label, key in (("Failures", "failures"), ("Modeling problems", "modeling_problems")):
        rows = s[key] or []
        if rows:
            blocks += [
                f"{label}:",
                _table(["Device", "Message"], [[r["device"], r["message"]] for r in rows]),
            ]
        else:
            blocks.append(f"{label}: none.")
    return blocks


_RENDERERS: dict[str, Any] = {
    "checks": _render_checks,
    "flows": _render_flows,
    "state": _render_state,
    "connectivity": _render_connectivity,
    "routing": _render_rows(
        ["Device", "Added", "Removed", "Modified"], ["device", "added", "removed", "modified"]
    ),
    "config": _render_rows(["Device", "Files changed"], ["device", "files_changed"]),
    "devices": _render_devices,
    "security_rules": _render_rows(["Device", "Rule", "Change"], ["device", "rule", "change"]),
    "vulnerabilities": _render_vulnerabilities,
    "diff_counts": _render_diff_counts,
    "predict": _render_predict,
    "ai_summary": lambda s: [s["text"]],
}

_SHOWN_ITEMS: dict[str, Any] = {
    "devices": lambda s: len(s["added"]) + len(s["removed"]) + len(s["modified"]),
    "connectivity": lambda s: sum(len(v) for v in s["location_pairs"].values()),
}


def render_markdown(report: Mapping[str, Any]) -> str:
    """The report as Markdown (see ``RULES.md``): verdict, sections, then a footer."""
    meta = report["meta"]
    where = (
        f"Base `{meta['base_snapshot_id']}` → after `{meta['after_snapshot_id']}` "
        f"({meta['after_kind']}), network `{meta['network_id']}`"
    )
    if meta.get("change_set_id"):
        where += f", change set `{meta['change_set_id']}`"
    blocks = ["# Change report", where + ".", f"**Verdict: {report['verdict']['result']}**"]
    reasons = report["verdict"]["reasons"]
    if reasons:
        blocks.append("\n".join(f"- {r['code']} ({r['section']}): {r['detail']}" for r in reasons))
    footer: list[str] = []
    for name, title in SECTIONS:
        section = report["sections"][name]
        status = section["status"]
        if status == "ok":
            blocks.append(f"## {title}")
            blocks += _RENDERERS[name](section)
            if section.get("truncated"):
                shown = _SHOWN_ITEMS.get(name, lambda s: len(s["items"]))(section)
                blocks.append(f"Showing {shown} of {section['total']}.")
        elif status == "unavailable":
            reason = section["reason"]
            footer.append(f"- {title}: unavailable ({reason['kind']}: {reason['message']})")
        else:
            footer.append(f"- {title}: {status.replace('_', ' ')}")
    blocks.append("---")
    if footer:
        blocks.append("\n".join(footer))
    sdk = meta["sdk"]
    blocks.append(
        f"Built from Forward's API by {sdk['name']} {sdk['version']}; not the Predict UI's report."
    )
    return "\n\n".join(blocks) + "\n"


@dataclass(frozen=True)
class Flow:
    """A flow to compare: a trace query, and whether it must keep a path.

    ``expectation`` is ``"valid"`` (losing every VALID path is a regression),
    ``"none"`` (it is meant to lose them, as for an isolation intent) or
    ``"any"`` (reported, never drives the verdict).
    """

    name: str
    query: PathQuery
    expectation: str = "valid"


@dataclass(frozen=True)
class ChangeReport:
    """A built report: ``data`` is the shared schema_version 1 JSON object."""

    data: dict[str, Any]

    @property
    def result(self) -> str:
        """``regression``, ``no_regression`` or ``incomplete``."""
        return str(self.data["verdict"]["result"])

    def to_json(self) -> str:
        return to_json(self.data)

    def to_markdown(self) -> str:
        return render_markdown(self.data)
