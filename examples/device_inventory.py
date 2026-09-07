"""Write a device inventory to CSV, paging rather than fetching everything at once."""

from __future__ import annotations

import csv
import sys

from forward_sdk import ForwardClient

COLUMNS = ["name", "type", "vendor", "model", "platform", "os_version"]


def main(destination: str = "devices.csv") -> None:
    with ForwardClient.from_env() as client, open(destination, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)

        count = 0
        for device in client.devices.iter(page_size=500):
            writer.writerow([_text(getattr(device, column, None)) for column in COLUMNS])
            count += 1

    print(f"wrote {count} devices to {destination}")


def _text(value: object) -> str:
    return "" if value is None else str(value)


if __name__ == "__main__":
    main(*sys.argv[1:])
