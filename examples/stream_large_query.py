"""Stream a large result set instead of holding it in memory.

Streaming yields rows as they arrive, so a query returning millions of rows uses
roughly constant memory. The trade-off is that a dropped connection cannot be
resumed; use `rows()` instead when the run must survive a blip.
"""

from __future__ import annotations

from collections import Counter

from forward_sdk import ForwardClient

QUERY = """
foreach device in network.devices
foreach interface in device.interfaces
select {
  device: device.name,
  interface: interface.name,
  status: interface.operStatus,
}
"""


def main() -> None:
    with ForwardClient.from_env() as client:
        execution = client.nqe.execute(QUERY)
        print(f"execution {execution.key} started; waiting...")
        execution.wait()

        by_status: Counter[str] = Counter()
        total = 0
        for row in execution.stream():
            by_status[str(row.get("status"))] += 1
            total += 1

        print(f"\n{total} interfaces")
        for status, count in by_status.most_common():
            print(f"  {status:<12} {count}")


if __name__ == "__main__":
    main()
