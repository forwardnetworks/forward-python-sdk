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
    Path("src/forward_sdk/_sync"),
    Path("tests/_sync"),
)

STEPS = (
    ("down-convert", ["scripts/downconvert_spec.py"]),
    ("models", ["scripts/gen_models.py"]),
    ("operations", ["scripts/gen_operations.py"]),
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
    diff = subprocess.run(
        ["git", "diff", "--stat", "--", *existing], capture_output=True, text=True, check=False
    )
    if diff.stdout.strip():
        print("Generated code is stale. Re-run the generators and commit the result:\n")
        print(diff.stdout)
        return 1

    print("generated code is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
