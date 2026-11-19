"""ChangeReport assemble and render, byte for byte against the goldens shared with Go."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from forward_sdk.change_report import ChangeReportInputError, assemble, render_markdown, to_json

ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "change_report"
CASES = sorted(p.name for p in ROOT.iterdir() if p.is_dir())


def test_the_manifest_pins_every_shared_file() -> None:
    lines = (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    listed = {}
    for line in lines:
        digest, _, path = line.partition("  ")
        listed[path] = digest
    expected = {"RULES.md", "schema.json"} | {
        f"{case}/{name}" for case in CASES for name in ("input.json", "report.json", "report.md")
    }
    assert set(listed) == expected
    assert [line.partition("  ")[2] for line in lines] == sorted(listed, key=lambda p: p.encode())
    for path, digest in listed.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path


@pytest.mark.parametrize("case", CASES)
def test_assemble_matches_the_golden_report(case: str) -> None:
    data = json.loads((ROOT / case / "input.json").read_text(encoding="utf-8"))
    assert to_json(assemble(data)) == (ROOT / case / "report.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("case", CASES)
def test_render_matches_the_golden_markdown(case: str) -> None:
    report = json.loads((ROOT / case / "report.json").read_text(encoding="utf-8"))
    assert render_markdown(report) == (ROOT / case / "report.md").read_text(encoding="utf-8")


def test_a_non_terminal_check_status_is_an_input_error() -> None:
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    data["sections"]["checks"]["items"][0]["after_status"] = "PROCESSING"
    with pytest.raises(ChangeReportInputError, match="still being computed"):
        assemble(data)


def test_a_missing_required_section_is_an_input_error() -> None:
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    del data["sections"]["routing"]
    with pytest.raises(ChangeReportInputError, match="routing"):
        assemble(data)


def test_a_predicted_only_section_on_a_collected_snapshot_is_an_input_error() -> None:
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    data["sections"]["predict"] = {
        "status": "ok",
        "processing_state": "PROCESSED",
        "failures": [],
        "modeling_problems": [],
    }
    with pytest.raises(ChangeReportInputError, match="predicted snapshot"):
        assemble(data)


def test_a_predicted_snapshot_without_its_predict_section_is_an_input_error() -> None:
    data = json.loads((ROOT / "no_regression" / "input.json").read_text(encoding="utf-8"))
    del data["sections"]["predict"]
    with pytest.raises(ChangeReportInputError, match="predict"):
        assemble(data)


def test_a_check_with_neither_status_is_an_input_error() -> None:
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    data["sections"]["checks"]["items"][0].update(base_status=None, after_status=None)
    with pytest.raises(ChangeReportInputError, match="neither"):
        assemble(data)


def test_an_unknown_section_status_or_a_bad_cap_is_an_input_error() -> None:
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    data["sections"]["routing"]["status"] = "partial"
    with pytest.raises(ChangeReportInputError, match="partial"):
        assemble(data)
    data = json.loads((ROOT / "regression" / "input.json").read_text(encoding="utf-8"))
    data["options"]["max_items"] = 0
    with pytest.raises(ChangeReportInputError, match="max_items"):
        assemble(data)


def _load(case: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / case / "input.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("case", "path", "duplicate"),
    [
        ("regression", ("checks", "items"), 0),
        ("mixed_changes", ("flows", "items"), 0),
        ("regression", ("routing", "items"), 0),
        ("predicted_security_rules", ("security_rules", "items"), 0),
    ],
)
def test_a_duplicate_identity_is_an_input_error(
    case: str, path: tuple[str, str], duplicate: int
) -> None:
    data = _load(case)
    rows = data["sections"][path[0]][path[1]]
    rows.append(dict(rows[duplicate]))
    with pytest.raises(ChangeReportInputError, match="duplicate"):
        assemble(data)


def test_a_duplicate_state_row_or_location_pair_or_device_is_an_input_error() -> None:
    data = _load("expected_removed")
    data["sections"]["state"]["base"].append(dict(data["sections"]["state"]["base"][0]))
    with pytest.raises(ChangeReportInputError, match="duplicate"):
        assemble(data)
    data = _load("regression")
    pairs = data["sections"]["connectivity"]["location_pairs"]["DELETED"]
    pairs.append(dict(pairs[0]))
    with pytest.raises(ChangeReportInputError, match="duplicate"):
        assemble(data)
    data = _load("regression")
    data["sections"]["devices"]["modified"].append("edge-1")
    with pytest.raises(ChangeReportInputError, match="duplicate"):
        assemble(data)


@pytest.mark.parametrize("expectation", [None, "maybe"])
def test_a_flow_needs_a_known_expectation(expectation: str | None) -> None:
    data = _load("mixed_changes")
    flow = data["sections"]["flows"]["items"][0]
    if expectation is None:
        del flow["expectation"]
    else:
        flow["expectation"] = expectation
    with pytest.raises(ChangeReportInputError, match="expectation"):
        assemble(data)


def test_a_flow_expecting_any_never_drives_the_verdict() -> None:
    data = _load("expected_removed")
    data["sections"]["flows"]["items"][1]["expectation"] = "any"  # loses its VALID path
    report = assemble(data)
    assert not any(r["code"] == "flow_lost_valid_path" for r in report["verdict"]["reasons"])


@pytest.mark.parametrize("text", ["", "ends with a newline\n"])
def test_an_ai_summary_must_be_one_non_empty_block(text: str) -> None:
    data = _load("edge_shapes")
    data["sections"]["ai_summary"]["text"] = text
    with pytest.raises(ChangeReportInputError, match="ai_summary"):
        assemble(data)
