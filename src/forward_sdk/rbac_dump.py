"""Write Forward's role table as JSON: ``python -m forward_sdk.rbac_dump --fwd <checkout>``.

For copying the table to a machine that has no checkout, where
``FORWARD_RBAC_TABLE`` can then name the file. See :mod:`forward_sdk.rbac`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forward_sdk.rbac import RoleTable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m forward_sdk.rbac_dump", description=__doc__)
    parser.add_argument("--fwd", type=Path, required=True, help="a checkout of Forward's source")
    args = parser.parse_args(argv)
    sys.stdout.write(RoleTable.from_checkout(args.fwd).to_json() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
