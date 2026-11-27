"""The scripts that vendor Forward's API description and generate code from it.

Two promises are checked. Each generator, run from the vendored spec, rebuilds
exactly what is checked in; and ``sync_spec.py`` vendors a Forward checkout's
description, records where it came from, reports the operations that changed and
then runs the whole pipeline.

Every run happens in a throwaway copy of the repository. What a run should write
is deleted first, or marked as stale where a later step reads it, so a script
that writes nothing cannot pass.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

COPIED = ("scripts", "spec", "src", "tests/_async", "tests/_sync", "tests/__init__.py")

#: For the tests that run generators. CI checks generated code on Linux, and these
#: have never been run on Windows; the vendoring tests below run everywhere.
runs_generators = pytest.mark.skipif(
    sys.platform == "win32", reason="the code generators have not been run on Windows"
)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    copy = tmp_path / "checkout"
    for name in (*COPIED, "pyproject.toml"):
        source, target = ROOT / name, copy / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(source, target)
    return copy


def clean_environment() -> dict[str, str]:
    """No inherited git state or personal git settings, and this interpreter's tools first.

    The generators format with whichever ``ruff`` is on ``PATH``.
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    tools = str(Path(sys.executable).parent)
    environment["PATH"] = tools + os.pathsep + environment.get("PATH", "")
    return environment


def run(checkout: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        cwd=checkout,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return result.stdout.strip()


def python_and_data(path: Path) -> dict[str, str]:
    """Every file at ``path`` (a file or a directory), by relative name."""
    if path.is_file():
        return {"": path.read_text(encoding="utf-8")}
    return {
        found.relative_to(path).as_posix(): found.read_text(encoding="utf-8")
        for found in sorted(path.rglob("*"))
        if found.is_file() and "__pycache__" not in found.parts
    }


def remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def assert_rebuilt(checkout: Path, output: str) -> None:
    """``output`` as rebuilt equals what is checked in."""
    rebuilt, checked_in = python_and_data(checkout / output), python_and_data(ROOT / output)
    assert checked_in, output
    assert sorted(rebuilt) == sorted(checked_in), output
    for name in sorted(rebuilt):
        if rebuilt[name] != checked_in[name]:
            changed = difflib.unified_diff(
                checked_in[name].splitlines(), rebuilt[name].splitlines(), lineterm="", n=1
            )
            pytest.fail(f"{output}/{name} is stale:\n" + "\n".join(list(changed)[:20]))


@runs_generators
@pytest.mark.parametrize(
    ("script", "output"),
    [
        ("downconvert_spec.py", "spec/forward-openapi-3.1.json"),
        ("gen_models.py", "src/forward_sdk/_generated/models.py"),
        ("gen_operations.py", "src/forward_sdk/_generated/operations.py"),
        ("gen_services.py", "src/forward_sdk/_async/services/_generated"),
        ("gen_public_models.py", "src/forward_sdk/models/__init__.py"),
        ("gen_nqe_enums.py", "src/forward_sdk/nqe/enums.py"),
    ],
)
def test_a_generator_rebuilds_what_is_checked_in(checkout: Path, script: str, output: str) -> None:
    remove(checkout / output)

    result = run(checkout, script)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_rebuilt(checkout, output)


# --------------------------------------------------------------------------- sync_spec.py


def description(*operations: tuple[str, str, str, bool]) -> str:
    """A tiny OpenAPI description: ``(operationId, method, path, deprecated)`` each."""
    paths: dict[str, dict[str, object]] = {}
    for operation_id, method, path, deprecated in operations:
        entry: dict[str, object] = {"operationId": operation_id}
        if deprecated:
            entry["deprecated"] = True
        paths.setdefault(path, {})[method] = entry
    return json.dumps(
        {"openapi": "3.2.0", "info": {"version": "${apiVersion}"}, "paths": paths}, indent=1
    )


def forward_root(tmp_path: Path, text: str, *, tag: str | None = None) -> Path:
    """A stand-in for the Forward checkout: the joined description, optionally tagged."""
    root = tmp_path / "fwd"
    joined = root / "api" / "build" / "joined" / "complete.yaml"
    joined.parent.mkdir(parents=True)
    joined.write_text(text, encoding="utf-8")
    if tag is not None:
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, *identity, "commit", "-q", "--no-verify", "--no-gpg-sign", "-m", "x")
        git(root, "tag", "--no-sign", tag)
    return root


def metadata(checkout: Path) -> dict[str, object]:
    loaded = json.loads((checkout / "spec" / "SPEC_SOURCE.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_sync_vendors_the_description_and_reports_what_changed(
    checkout: Path, tmp_path: Path
) -> None:
    (checkout / "spec" / "forward-openapi.yaml").write_text(
        description(
            ("kept", "get", "/kept", False),
            ("aged", "get", "/aged", False),
            ("gone", "delete", "/gone", False),
        ),
        encoding="utf-8",
    )
    new = description(
        ("kept", "get", "/kept", False),
        ("aged", "get", "/aged", True),
        ("fresh", "post", "/fresh", False),
    )
    root = forward_root(tmp_path, new, tag="26.4.1")
    schema = root / "docs" / ".generated" / "nqe-network-schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text('{"marker": "new data model"}\n', encoding="utf-8")

    result = run(checkout, "sync_spec.py", "--fwd-root", str(root), "--skip-generators")

    assert result.returncode == 0, result.stdout + result.stderr
    vendored = (checkout / "spec" / "forward-openapi.yaml").read_text(encoding="utf-8")
    assert vendored == new.replace("${apiVersion}", "26.4.1")
    assert (checkout / "spec" / "nqe-network-schema.json").read_text(encoding="utf-8") == (
        '{"marker": "new data model"}\n'
    )
    recorded = metadata(checkout)
    assert recorded["fwd_commit"] == git(root, "rev-parse", "HEAD")
    assert recorded["api_version"] == "26.4.1"
    assert recorded["openapi"] == "3.2.0"
    assert recorded["operations"] == 3
    assert recorded["sha256"] == hashlib.sha256(vendored.encode()).hexdigest()
    changes = result.stdout.split("operation changes since last sync", 1)[1].splitlines()
    assert [line.strip() for line in changes if line.strip().startswith(("+", "-", "~"))] == [
        "+ fresh: POST /fresh",
        "- gone: DELETE /gone",
        "~ aged: GET /aged -> GET /aged (deprecated)",
    ]


@pytest.mark.parametrize(
    ("tag", "flags", "expected"),
    [
        ("nightly-build", (), "unreleased"),
        ("nightly-build", ("--api-version", "26.9.0"), "26.9.0"),
    ],
)
def test_sync_never_publishes_an_internal_build_name(
    checkout: Path, tmp_path: Path, tag: str, flags: tuple[str, ...], expected: str
) -> None:
    root = forward_root(tmp_path, description(("kept", "get", "/kept", False)), tag=tag)
    # The checked-in record may already hold the expected answer.
    (checkout / "spec" / "SPEC_SOURCE.json").unlink()

    result = run(checkout, "sync_spec.py", "--fwd-root", str(root), "--skip-generators", *flags)

    assert result.returncode == 0, result.stdout + result.stderr
    assert metadata(checkout)["api_version"] == expected
    assert tag not in (checkout / "spec" / "forward-openapi.yaml").read_text(encoding="utf-8")
    assert tag not in (checkout / "spec" / "SPEC_SOURCE.json").read_text(encoding="utf-8")


def test_sync_says_how_to_build_a_missing_description(checkout: Path, tmp_path: Path) -> None:
    before = (checkout / "spec" / "forward-openapi.yaml").read_text(encoding="utf-8")

    result = run(checkout, "sync_spec.py", "--fwd-root", str(tmp_path / "nowhere"))

    assert result.returncode != 0
    assert "./gradlew :api:joinApis" in result.stderr
    assert (checkout / "spec" / "forward-openapi.yaml").read_text(encoding="utf-8") == before


@runs_generators
def test_a_full_sync_runs_every_generator(checkout: Path, tmp_path: Path) -> None:
    current = (ROOT / "spec" / "forward-openapi.yaml").read_text(encoding="utf-8")
    root = forward_root(tmp_path, current)
    outputs = (
        "spec/forward-openapi-3.1.json",
        "src/forward_sdk/_generated/models.py",
        "src/forward_sdk/_generated/operations.py",
        "src/forward_sdk/_async/services/_generated",
        "src/forward_sdk/models/__init__.py",
        "src/forward_sdk/nqe/enums.py",
        "src/forward_sdk/_sync",
    )
    # Later generators read earlier ones, so these stay in place, each marked as stale:
    # a step that does not run leaves its mark behind.
    for output in (
        "src/forward_sdk/_generated/models.py",
        "src/forward_sdk/_generated/operations.py",
        "src/forward_sdk/_async/services/_generated/__init__.py",
        "src/forward_sdk/models/__init__.py",
        "src/forward_sdk/nqe/enums.py",
    ):
        with (checkout / output).open("a", encoding="utf-8") as handle:
            handle.write("\n# stale\n")
    remove(checkout / "spec" / "forward-openapi-3.1.json")
    remove(checkout / "src" / "forward_sdk" / "_sync")

    result = run(checkout, "sync_spec.py", "--fwd-root", str(root), "--api-version", "unreleased")

    assert result.returncode == 0, result.stdout + result.stderr
    for output in outputs:
        assert_rebuilt(checkout, output)
