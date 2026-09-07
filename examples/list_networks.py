"""List networks and their most recent snapshot."""

from __future__ import annotations

from forward_sdk import ForwardClient


def main() -> None:
    with ForwardClient.from_env() as client:
        version = client.version()
        print(f"Connected to Forward {version.get('version', 'unknown')}\n")

        for network in client.networks.list():
            snapshot = client.snapshots.latest_processed(network.id)
            label = f"{network.name} ({network.id})"
            if snapshot:
                print(f"{label}: snapshot {snapshot.id}, processed {snapshot.processed_at}")
            else:
                print(f"{label}: no processed snapshot")


if __name__ == "__main__":
    main()
