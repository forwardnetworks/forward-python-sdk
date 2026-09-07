"""The asynchronous client, running independent queries concurrently."""

from __future__ import annotations

import asyncio

from forward_sdk import AsyncForwardClient

DEVICE_COUNT = "foreach d in network.devices select {name: d.name}"
INTERFACE_COUNT = """
foreach d in network.devices
foreach i in d.interfaces
select {device: d.name, interface: i.name}
"""


async def main() -> None:
    async with AsyncForwardClient.from_env() as client:
        networks, devices, interfaces = await asyncio.gather(
            client.networks.list(),
            client.nqe.query(DEVICE_COUNT),
            client.nqe.query(INTERFACE_COUNT),
        )

        print(f"networks:   {len(networks)}")
        print(f"devices:    {len(devices)}")
        print(f"interfaces: {len(interfaces)}")

        counters = client.counters
        print(f"\n{counters.http_attempts} requests, {counters.nqe_rows} rows")


if __name__ == "__main__":
    asyncio.run(main())
