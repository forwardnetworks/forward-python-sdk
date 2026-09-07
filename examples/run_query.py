"""Run an NQE query and print the rows.

Rows are plain dictionaries whose keys are the columns the query selected.
"""

from __future__ import annotations

from forward_sdk import ForwardClient

QUERY = """
foreach device in network.devices
where device.platform.osVersion != ""
select {
  name: device.name,
  vendor: device.platform.vendor,
  os: device.platform.osVersion,
}
"""


def main() -> None:
    with ForwardClient.from_env() as client:
        # Omitting snapshot_id runs against the latest processed snapshot.
        rows = client.nqe.query(QUERY)

        print(f"{len(rows)} devices\n")
        for row in rows[:20]:
            print(f"  {row['name']:<30} {row['vendor']:<12} {row['os']}")

        print(f"\nrequests: {client.counters.http_attempts}")


if __name__ == "__main__":
    main()
