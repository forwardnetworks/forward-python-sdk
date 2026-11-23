"""ADR-2611230746: the spec-first process is checked by the repository's own tests.

One test per scenario of docs/specs/ADR-2611230746.json, written from the spec
alone, against the checker in scripts/check_design_records.py. Each scenario
builds the records it describes in a temporary repository. The last test runs
the checker on this repository itself.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def checker() -> ModuleType:
    """The checker under test, imported per test so each fails alone until it exists."""
    return importlib.import_module("scripts.check_design_records")


HASH = "a" * 64
CI_OK = """\
name: CI
"on":
  push:
    branches: [main]
  pull_request:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: uv run mypy
      - run: uv run pytest -q
"""


def adr(root: Path, adr_id: str, status: str) -> None:
    path = root / "docs" / "adrs" / f"{adr_id}-some-title.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {adr_id}: A title\n\n**Status:** {status}\n**Date:** 2026-11-23\n")


def spec(root: Path, name: str, body: dict[str, Any]) -> None:
    path = root / "docs" / "specs" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))


def bound(
    test: str = "tests/unit/test_x.py::test_a", red: str | None = "a1b2c3d"
) -> dict[str, Any]:
    scenario: dict[str, Any] = {"id": "S01", "decision": ["§1"], "kind": "example", "test": test}
    if red is not None:
        scenario["red"] = red
    return scenario


def unbound() -> dict[str, Any]:
    return {"id": "S02", "decision": ["§1"], "kind": "negative"}


def write_test_file(root: Path, rel: str, *functions: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"def {name}() -> None:\n    pass\n\n\n" for name in functions))


def ci(root: Path, text: str) -> None:
    path = root / ".github" / "workflows" / "ci.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# §1 ------------------------------------------------------------------------


def test_s01_an_accepted_adr_with_a_matching_spec_passes(tmp_path: Path) -> None:
    adr(tmp_path, "ADR-2611221115", "Accepted")
    spec(tmp_path, "ADR-2611221115", {"adr": "ADR-2611221115", "specs": []})
    assert checker().check_specs(tmp_path) == []


def test_s02_proposed_and_superseded_adrs_need_no_spec(tmp_path: Path) -> None:
    adr(tmp_path, "ADR-2611230800", "Proposed")
    adr(tmp_path, "ADR-2611221107", "Superseded")
    assert checker().check_specs(tmp_path) == []


def test_s03_a_completed_adr_without_a_spec_is_refused(tmp_path: Path) -> None:
    adr(tmp_path, "ADR-2611230801", "Completed")
    failures = checker().check_specs(tmp_path)
    assert failures and any("ADR-2611230801" in f and "spec" in f for f in failures)


def test_s04_a_spec_naming_a_different_adr_is_refused(tmp_path: Path) -> None:
    adr(tmp_path, "ADR-2611230802", "Accepted")
    spec(tmp_path, "ADR-2611230802", {"adr": "ADR-2611230803", "specs": []})
    failures = checker().check_specs(tmp_path)
    assert failures and any("ADR-2611230802" in f and "ADR-2611230803" in f for f in failures)


def test_s05_adr_2611221107_is_the_only_exemption(tmp_path: Path) -> None:
    adr(tmp_path, "ADR-2611221107", "Accepted")
    assert checker().check_specs(tmp_path) == []
    adr(tmp_path, "ADR-2611221108", "Accepted")
    failures = checker().check_specs(tmp_path)
    assert any("ADR-2611221108" in f for f in failures)
    assert not any("ADR-2611221107" in f for f in failures)


def test_s06_every_accepted_or_completed_adr_but_the_exempt_one_has_its_own_spec(
    tmp_path: Path,
) -> None:
    cases = [
        # (status, spec's adr field or None for no spec, must fail)
        ("Accepted", "self", False),
        ("Completed", "self", False),
        ("Accepted", None, True),
        ("Completed", "other", True),
        ("Proposed", None, False),
        ("Superseded", None, False),
        ("Abandoned", "other", False),
    ]
    for number, (status, field, must_fail) in enumerate(cases):
        root = tmp_path / str(number)
        adr_id = f"ADR-26112308{number:02d}"
        adr(root, adr_id, status)
        if field is not None:
            spec(
                root, adr_id, {"adr": adr_id if field == "self" else "ADR-2600000000", "specs": []}
            )
        assert bool(checker().check_specs(root)) is must_fail, (status, field)


# §2 ------------------------------------------------------------------------


def test_s07_a_bound_spec_with_a_full_acceptance_record_passes(tmp_path: Path) -> None:
    spec(
        tmp_path,
        "ADR-1",
        {"adr": "ADR-1", "accepted": {"by": "C. Johns", "sha256": HASH}, "specs": [bound()]},
    )
    assert checker().check_acceptance(tmp_path) == []


def test_s08_a_spec_with_no_bound_scenarios_needs_no_acceptance(tmp_path: Path) -> None:
    spec(tmp_path, "ADR-1", {"adr": "ADR-1", "specs": [unbound()]})
    assert checker().check_acceptance(tmp_path) == []


def test_s09_a_bound_spec_with_no_accepted_record_is_refused(tmp_path: Path) -> None:
    spec(tmp_path, "ADR-1", {"adr": "ADR-1", "specs": [bound()]})
    failures = checker().check_acceptance(tmp_path)
    assert failures and any("ADR-1" in f and "accept" in f for f in failures)


def test_s10_an_empty_acceptor_is_refused(tmp_path: Path) -> None:
    spec(
        tmp_path,
        "ADR-1",
        {"adr": "ADR-1", "accepted": {"by": "", "sha256": HASH}, "specs": [bound()]},
    )
    assert checker().check_acceptance(tmp_path)


def test_s11_a_malformed_hash_is_refused(tmp_path: Path) -> None:
    for sha in ("a" * 63, "a" * 63 + "g"):
        root = tmp_path / str(len(sha)) / sha[-1]
        spec(
            root,
            "ADR-1",
            {"adr": "ADR-1", "accepted": {"by": "C", "sha256": sha}, "specs": [bound()]},
        )
        assert checker().check_acceptance(root), sha


def test_s12_acceptance_is_required_exactly_when_something_is_bound(tmp_path: Path) -> None:
    good = {"by": "C", "sha256": "0123456789abcdefABCDEF" + "0" * 42}
    cases: list[tuple[dict[str, Any] | None, list[dict[str, Any]], bool]] = [
        (None, [unbound()], False),
        ({"by": "", "sha256": "x"}, [unbound()], False),
        (None, [unbound(), bound()], True),
        (good, [unbound(), bound()], False),
        ({"by": "C", "sha256": "z" * 64}, [bound()], True),
        ({"by": "C", "sha256": "a" * 65}, [bound()], True),
        ({"by": "C"}, [bound()], True),
    ]
    for number, (accepted, scenarios, must_fail) in enumerate(cases):
        root = tmp_path / str(number)
        body: dict[str, Any] = {"adr": "ADR-1", "specs": scenarios}
        if accepted is not None:
            body["accepted"] = accepted
        spec(root, "ADR-1", body)
        assert bool(checker().check_acceptance(root)) is must_fail, (accepted, scenarios)


# §3 ------------------------------------------------------------------------


def test_s13_a_scenario_bound_to_an_existing_test_with_a_red_commit_passes(tmp_path: Path) -> None:
    rel = "tests/unit/test_design_records.py"
    write_test_file(tmp_path, rel, "test_every_accepted_adr_has_a_spec")
    spec(
        tmp_path,
        "ADR-1",
        {"adr": "ADR-1", "specs": [bound(f"{rel}::test_every_accepted_adr_has_a_spec", "a1b2c3d")]},
    )
    assert checker().check_bindings(tmp_path) == []


def test_s14_a_renamed_test_is_refused(tmp_path: Path) -> None:
    write_test_file(tmp_path, "tests/unit/test_x.py", "test_new")
    spec(tmp_path, "ADR-1", {"adr": "ADR-1", "specs": [bound("tests/unit/test_x.py::test_old")]})
    failures = checker().check_bindings(tmp_path)
    assert failures and any("S01" in f and "test_old" in f for f in failures)


def test_s15_a_test_file_outside_tests_or_missing_is_refused(tmp_path: Path) -> None:
    for number, ref in enumerate(("src/check.py::test_a", "tests/unit/test_gone.py::test_a")):
        root = tmp_path / str(number)  # not the ref: ":" is not allowed in a Windows path
        write_test_file(root, "src/check.py", "test_a")
        spec(root, "ADR-1", {"adr": "ADR-1", "specs": [bound(ref)]})
        assert checker().check_bindings(root), ref


def test_s16_a_missing_or_malformed_red_commit_is_refused(tmp_path: Path) -> None:
    for number, red in enumerate((None, "a1b2c3", "a" * 41, "zzzzzzz")):
        root = tmp_path / str(number)
        write_test_file(root, "tests/unit/test_x.py", "test_a")
        spec(root, "ADR-1", {"adr": "ADR-1", "specs": [bound("tests/unit/test_x.py::test_a", red)]})
        assert checker().check_bindings(root), red


def test_s17_every_binding_resolves_and_carries_a_red_commit_id(tmp_path: Path) -> None:
    cases: list[tuple[list[dict[str, Any]], bool]] = [
        ([unbound()], False),
        ([bound("tests/unit/test_x.py::test_a", "a" * 7)], False),
        ([bound("tests/unit/test_x.py::test_a", "A" * 40)], False),
        ([bound("tests/unit/test_x.py::test_a", "a" * 40), unbound()], False),
        ([bound("tests/unit/test_x.py::test_b", "a" * 7)], True),
        (
            [
                bound("tests/unit/test_x.py::test_a", "a" * 7),
                bound("tests/unit/test_y.py::test_a", "a" * 7),
            ],
            True,
        ),
    ]
    for number, (scenarios, must_fail) in enumerate(cases):
        root = tmp_path / str(number)
        write_test_file(root, "tests/unit/test_x.py", "test_a")
        spec(root, "ADR-1", {"adr": "ADR-1", "specs": scenarios})
        assert bool(checker().check_bindings(root)) is must_fail, scenarios


def test_s17_an_async_test_function_counts_as_defined(tmp_path: Path) -> None:
    path = tmp_path / "tests" / "_async" / "test_x.py"
    path.parent.mkdir(parents=True)
    path.write_text("async def test_a() -> None:\n    pass\n")
    spec(tmp_path, "ADR-1", {"adr": "ADR-1", "specs": [bound("tests/_async/test_x.py::test_a")]})
    assert checker().check_bindings(tmp_path) == []


# §4 ------------------------------------------------------------------------


def test_s18_a_workflow_running_both_tools_on_push_and_pull_request_passes(tmp_path: Path) -> None:
    ci(tmp_path, CI_OK)
    assert checker().check_ci(tmp_path) == []


def test_s19_a_workflow_missing_the_type_checker_is_refused(tmp_path: Path) -> None:
    ci(tmp_path, CI_OK.replace("      - run: uv run mypy\n", ""))
    failures = checker().check_ci(tmp_path)
    assert failures and any("mypy" in f for f in failures)


def test_s20_a_workflow_missing_the_test_suite_is_refused(tmp_path: Path) -> None:
    ci(tmp_path, CI_OK.replace("      - run: uv run pytest -q\n", ""))
    failures = checker().check_ci(tmp_path)
    assert failures and any("pytest" in f for f in failures)


def test_s21_a_workflow_that_does_not_run_on_pull_requests_is_refused(tmp_path: Path) -> None:
    ci(tmp_path, CI_OK.replace("  pull_request:\n", ""))
    failures = checker().check_ci(tmp_path)
    assert failures and any("pull_request" in f for f in failures)


def test_s21_a_workflow_that_does_not_run_on_push_to_main_is_refused(tmp_path: Path) -> None:
    ci(tmp_path, CI_OK.replace("    branches: [main]\n", "    branches: [release]\n"))
    assert checker().check_ci(tmp_path)


def test_s22_a_missing_workflow_is_refused(tmp_path: Path) -> None:
    assert checker().check_ci(tmp_path)


# §5 ------------------------------------------------------------------------

ALLOWED = [
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".python-version",
]


def test_s23_only_the_allowed_dot_paths_are_tracked() -> None:
    assert (
        checker().check_tracked([*ALLOWED, "src/forward_sdk/__init__.py", "tests/conftest.py"])
        == []
    )


def test_s24_a_committed_tool_folder_is_refused() -> None:
    for path, segment in (
        (".vscode/settings.json", ".vscode"),
        (".claude/settings.local.json", ".claude"),
    ):
        failures = checker().check_tracked([*ALLOWED, path])
        assert failures and any(segment in f for f in failures), path


def test_s25_a_near_match_of_an_allowed_name_is_refused() -> None:
    for path in (".python-version.bak", ".github-old/x"):
        assert checker().check_tracked([*ALLOWED, path]), path


def test_s26_dot_files_below_the_top_level_are_not_restricted() -> None:
    assert checker().check_tracked([*ALLOWED, "tests/fixtures/.keep"]) == []


def test_s27_the_tracked_dot_segments_are_a_subset_of_the_allow_list() -> None:
    cases = [
        ([], False),
        (ALLOWED, False),
        (["a/.b/c", "x/.y"], False),
        ([".env"], True),
        ([".github/x", ".githubx"], True),
        (["./src/x.py"], True),
    ]
    for paths, must_fail in cases:
        assert bool(checker().check_tracked(paths)) is must_fail, paths


# This repository -----------------------------------------------------------


def test_this_repository_keeps_every_record_the_process_requires() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    assert checker().check_all(ROOT, tracked) == []
