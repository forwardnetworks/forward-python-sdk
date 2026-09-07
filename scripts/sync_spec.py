"""Refresh the vendored Forward OpenAPI description and everything derived from it.

The Forward monorepo generates its OpenAPI description from the server's own
controllers and gates that in CI (``./gradlew :web:testApisUpToDate``), so the
description cannot drift from the running API. This script vendors that
description here and regenerates the models and the operation table from it.

Usage::

    uv run python scripts/sync_spec.py --fwd-root ~/src/fwd

The printed operation diff is the starting point for a changelog entry: it lists
exactly which operations a Forward release added, removed or deprecated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

JOINED = Path("api/build/joined/complete.yaml")
#: Forward's NQE data model, from which its published data-model pages are
#: generated. A separate namespace from the REST schema, and the source for
#: enum member names used in query predicates.
NQE_SCHEMA = Path("docs/.generated/nqe-network-schema.json")
VENDORED_NQE = Path("spec/nqe-network-schema.json")
VENDORED = Path("spec/forward-openapi.yaml")
SOURCE_METADATA = Path("spec/SPEC_SOURCE.json")

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


#: A Forward release version looks like "26.4.1". Anything else -- a snapshot
#: build, an internal branch tag -- is not a version anyone outside Forward can
#: interpret, and internal build names do not belong in a published artifact.
RELEASE_VERSION = re.compile(r"^\d+\.\d+(\.\d+)?([.-]\w+)?$")

UNRELEASED = "unreleased"


def _release_version(root: Path) -> str:
    """The Forward release this description came from, if it is identifiable.

    Pass ``--api-version`` when building from a checkout that is not on a
    release tag; otherwise the version is recorded as unreleased rather than
    guessed from an internal build name.
    """
    for candidate in (
        git(root, "describe", "--tags", "--abbrev=0", "--match", "[0-9]*"),
        git(root, "describe", "--tags", "--abbrev=0"),
    ):
        if candidate and RELEASE_VERSION.match(candidate):
            return candidate
    return UNRELEASED


def operation_summary(doc: dict[str, Any]) -> dict[str, str]:
    """Map ``operationId`` to a stable ``METHOD /path`` label, for diffing."""
    summary: dict[str, str] = {}
    for path, item in (doc.get("paths") or {}).items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                label = f"{method.upper()} {path}"
                if operation.get("deprecated"):
                    label += " (deprecated)"
                summary[operation["operationId"]] = label
    return summary


def report_diff(before: dict[str, str], after: dict[str, str]) -> None:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    if not (added or removed or changed):
        print("  no operation changes")
        return
    for name in added:
        print(f"  + {name}: {after[name]}")
    for name in removed:
        print(f"  - {name}: {before[name]}")
    for name in changed:
        print(f"  ~ {name}: {before[name]} -> {after[name]}")


def run_step(description: str, argv: list[str]) -> None:
    print(f"\n== {description}")
    result = subprocess.run([sys.executable, *argv], check=False)
    if result.returncode != 0:
        raise SystemExit(f"{description} failed with exit code {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fwd-root",
        type=Path,
        default=Path.home() / "src" / "fwd",
        help="checkout of the Forward monorepo",
    )
    parser.add_argument(
        "--api-version",
        default=None,
        help="value to substitute for the spec's ${apiVersion} placeholder",
    )
    parser.add_argument("--skip-generators", action="store_true", help="vendor the spec only")
    args = parser.parse_args(argv)

    source = args.fwd_root / JOINED
    if not source.exists():
        raise SystemExit(
            f"{source} not found. Build it in the Forward checkout first:\n"
            f"    ./gradlew :api:joinApis"
        )

    previous: dict[str, str] = {}
    if VENDORED.exists():
        previous = operation_summary(yaml.safe_load(VENDORED.read_text(encoding="utf-8")))

    text = source.read_text(encoding="utf-8")
    commit = git(args.fwd_root, "rev-parse", "HEAD")
    api_version = args.api_version or _release_version(args.fwd_root)

    # The upstream description carries a build-time placeholder; a literal
    # "${apiVersion}" makes the document invalid for strict validators.
    text = text.replace("${apiVersion}", api_version)
    VENDORED.write_text(text, encoding="utf-8")

    doc = yaml.safe_load(text)
    SOURCE_METADATA.write_text(
        json.dumps(
            {
                "fwd_commit": commit,
                "api_version": api_version,
                "openapi": doc.get("openapi"),
                "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "operations": len(operation_summary(doc)),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    nqe_source = args.fwd_root / NQE_SCHEMA
    if nqe_source.exists():
        VENDORED_NQE.write_text(nqe_source.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"vendored {nqe_source} -> {VENDORED_NQE}")
    else:
        print(f"note: {nqe_source} not found; NQE enums keep their current copy")

    print(f"vendored {source} -> {VENDORED}")
    print(f"  fwd {commit[:12]}, api version {api_version}")
    print("\n== operation changes since last sync")
    report_diff(previous, operation_summary(doc))

    if not args.skip_generators:
        run_step("down-convert to OpenAPI 3.1", ["scripts/downconvert_spec.py"])
        run_step("generate models", ["scripts/gen_models.py"])
        run_step("generate operation table", ["scripts/gen_operations.py"])
        run_step("generate service classes", ["scripts/gen_services.py"])
        run_step("generate public models", ["scripts/gen_public_models.py"])
        run_step("generate NQE enums", ["scripts/gen_nqe_enums.py"])
        run_step("generate the sync client", ["scripts/unasync.py"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
