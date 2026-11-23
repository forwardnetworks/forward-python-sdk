"""Check the records the spec-first process leaves in the repository (ADR-2611230746).

Each check returns a list of failures, one sentence each naming what is wrong;
an empty list is a pass. Run it directly to check this repository::

    uv run python scripts/check_design_records.py
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ALLOWED_DOT_SEGMENTS",
    "check_acceptance",
    "check_all",
    "check_bindings",
    "check_ci",
    "check_specs",
    "check_tracked",
]

#: The ADR that decided the process in terms of what people do; it has no spec.
EXEMPT = "ADR-2611221107"
#: Tracked first path segments that may start with a dot (ADR-2611230746 §5).
ALLOWED_DOT_SEGMENTS = frozenset(
    {".gitattributes", ".github", ".gitignore", ".pre-commit-config.yaml", ".python-version"}
)

_ADR_FILE = re.compile(r"^(ADR-\d{10})(?:-.*)?\.md$")
_STATUS = re.compile(r"^\*\*Status:\*\*\s*(\w+)", re.MULTILINE)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")


def _specs(root: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    folder = root / "docs" / "specs"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        body = json.loads(path.read_text(encoding="utf-8"))
        yield path.stem, body if isinstance(body, dict) else {}


def _scenarios(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in body.get("specs") or [] if isinstance(s, dict)]


def check_specs(root: Path) -> list[str]:
    """§1: every Accepted or Completed ADR but the exempt one has its own spec."""
    failures = []
    folder = root / "docs" / "adrs"
    for path in sorted(folder.glob("ADR-*.md")) if folder.is_dir() else ():
        match = _ADR_FILE.match(path.name)
        status = _STATUS.search(path.read_text(encoding="utf-8"))
        if match is None or status is None:
            continue
        adr_id = match.group(1)
        if adr_id == EXEMPT or status.group(1).lower() not in ("accepted", "completed"):
            continue
        spec = root / "docs" / "specs" / f"{adr_id}.json"
        if not spec.is_file():
            failures.append(
                f"{adr_id} is {status.group(1)} but has no spec at docs/specs/{adr_id}.json"
            )
            continue
        body = json.loads(spec.read_text(encoding="utf-8"))
        named = body.get("adr") if isinstance(body, dict) else None
        if named != adr_id:
            failures.append(f"{adr_id}'s spec names {named} as its ADR")
    return failures


def check_acceptance(root: Path) -> list[str]:
    """§2: a spec that binds a test records who accepted it and a sha256."""
    failures = []
    for name, body in _specs(root):
        if not any("test" in s for s in _scenarios(body)):
            continue
        accepted = body.get("accepted")
        if not isinstance(accepted, dict):
            failures.append(f"{name} binds tests but was never accepted")
            continue
        if not isinstance(accepted.get("by"), str) or not accepted["by"]:
            failures.append(f"{name}'s acceptance names no person")
        sha = accepted.get("sha256")
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            failures.append(f"{name}'s acceptance has no 64-character hexadecimal sha256")
    return failures


def _defines(path: Path, function: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        for node in ast.walk(tree)
    )


def check_bindings(root: Path) -> list[str]:
    """§3: every bound test exists under tests/ and records a red commit id."""
    failures = []
    for name, body in _specs(root):
        for scenario in _scenarios(body):
            if "test" not in scenario:
                continue
            where = f"{name} {scenario.get('id', '?')}"
            file, _, function = str(scenario["test"]).partition("::")
            path = root / file
            if file.split("/")[0] != "tests" or ".." in file.split("/") or not path.is_file():
                failures.append(f"{where} names {file}, which is not a file under tests/")
            elif not function or not _defines(path, function):
                failures.append(
                    f"{where} names {function} in {file}, which defines no such function"
                )
            red = scenario.get("red")
            if not isinstance(red, str) or not _COMMIT.fullmatch(red):
                failures.append(
                    f"{where} records no red commit id (7 to 40 hexadecimal characters)"
                )
    return failures


def _steps(workflow: dict[Any, Any]) -> Iterable[str]:
    for job in (workflow.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield step["run"]


def check_ci(root: Path) -> list[str]:
    """§4: CI runs mypy and pytest on every push to main and every pull request."""
    path = root / ".github" / "workflows" / "ci.yml"
    if not path.is_file():
        return ["no CI workflow at .github/workflows/ci.yml"]
    workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # YAML 1.1 reads an unquoted `on` key as True.
    triggers = workflow.get("on", workflow.get(True)) or {}
    if isinstance(triggers, (str, list)):
        triggers = dict.fromkeys([triggers] if isinstance(triggers, str) else triggers)
    failures = []
    push = triggers.get("push", False)
    branches = (push or {}).get("branches") if isinstance(push, dict) else None
    if "push" not in triggers or (branches is not None and "main" not in branches):
        failures.append("CI does not run on push to main")
    if "pull_request" not in triggers:
        failures.append("CI does not run on pull_request")
    runs = list(_steps(workflow))
    for tool in ("mypy", "pytest"):
        if not any(re.search(rf"\buv run {tool}\b", run) for run in runs):
            failures.append(f"CI has no step running uv run {tool}")
    return failures


def check_tracked(paths: Iterable[str]) -> list[str]:
    """§5: no tracked path starts with a dot segment outside the allow-list."""
    bad = sorted({p.split("/")[0] for p in paths if p.startswith(".")} - ALLOWED_DOT_SEGMENTS)
    return [f"{segment} is tracked but is not an allowed dot path" for segment in bad]


def check_all(root: Path, tracked: Iterable[str]) -> list[str]:
    """Every check, on one repository."""
    return [
        *check_specs(root),
        *check_acceptance(root),
        *check_bindings(root),
        *check_ci(root),
        *check_tracked(tracked),
    ]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    failures = check_all(root, tracked)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
