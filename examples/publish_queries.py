"""Publish .nqe files to the organization's query library.

Keeps the queries an integration ships in step with the code that consumes them.

These operations use endpoints Forward does not publish; see the documentation
on unpublished endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

from forward_sdk import ForwardClient
from forward_sdk.nqe.files import iter_query_files, load_query

LIBRARY_DIRECTORY = "/MyOrg"


def main(source_directory: str = "queries") -> None:
    directory = Path(source_directory)
    files = {
        f"{LIBRARY_DIRECTORY}/{path.stem}": load_query(path, for_execution=False)
        for path in iter_query_files(directory)
    }
    if not files:
        print(f"no .nqe files in {directory}")
        return

    with ForwardClient.from_env() as client:
        snapshot = client.snapshots.latest_processed()
        report = client.nqe.repo.publish(
            files,
            title=f"Publish {len(files)} queries",
            body="Published by examples/publish_queries.py",
            # Validating against a real snapshot catches a query that no longer
            # compiles before anyone else sees it.
            dry_run_snapshot_id=snapshot.id if snapshot else None,
        )

    for path in report.committed_paths:
        print(f"  published {path}")
    for path in report.skipped_paths:
        print(f"  unchanged {path}")


if __name__ == "__main__":
    main(*sys.argv[1:])
