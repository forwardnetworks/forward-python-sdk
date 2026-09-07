"""Fail if checked-in generated code is stale.

Regenerates everything derived from the vendored spec and reports any file that
changed. CI runs this so a hand-edit of generated code, or a spec sync whose
generators were never re-run, cannot land.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

GENERATED = (
    Path("spec/forward-openapi-3.1.json"),
    Path("src/forward_sdk/_generated/models.py"),
    Path("src/forward_sdk/_generated/operations.py"),
    Path("src/forward_sdk/models/__init__.py"),
    Path("src/forward_sdk/nqe/enums.py"),
    Path("src/forward_sdk/_async/services/_generated"),
    Path("src/forward_sdk/_sync"),
    Path("tests/_sync"),
)

# Ordered: services are generated from the operation table, and the sync tree
# from everything above it.
STEPS = (
    ("down-convert", ["scripts/downconvert_spec.py"]),
    ("models", ["scripts/gen_models.py"]),
    ("operations", ["scripts/gen_operations.py"]),
    ("services", ["scripts/gen_services.py"]),
    ("public models", ["scripts/gen_public_models.py"]),
    ("nqe enums", ["scripts/gen_nqe_enums.py"]),
    ("unasync", ["scripts/unasync.py"]),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="only run the unasync step (used as a pre-commit hook)",
    )
    args = parser.parse_args(argv)

    steps = [s for s in STEPS if not args.fast or s[0] == "unasync"]
    for name, argv_step in steps:
        script = Path(argv_step[0])
        if not script.exists():
            print(f"skipping {name}: {script} does not exist yet")
            continue
        result = subprocess.run(
            [sys.executable, *argv_step], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit(f"generator {name} failed")

    existing = [str(p) for p in GENERATED if p.exists()]
    # `git status --porcelain` rather than `git diff`: a newly generated file is
    # untracked, and a diff would not mention it at all.
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *existing],
        capture_output=True,
        text=True,
        check=False,
    )
    if status.stdout.strip():
        print("Generated code is stale. Re-run the generators and commit the result:\n")
        print(status.stdout)
        return 1

    print("generated code is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
