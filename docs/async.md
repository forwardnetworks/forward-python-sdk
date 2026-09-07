# Asynchronous use

`AsyncForwardClient` mirrors `ForwardClient` exactly: same constructor, same
services, same method names.

```python
import asyncio
from forward_sdk import AsyncForwardClient


async def main() -> None:
    async with AsyncForwardClient.from_env() as client:
        networks = await client.networks.list()

        execution = await client.nqe.execute("foreach d in network.devices select {n: d.name}")
        await execution.wait()
        async for row in execution.stream():
            print(row)


asyncio.run(main())
```

Iterators become async iterators:

```python
async for device in client.devices.iter(network_id="101"):
    ...
async for row in execution.rows():
    ...
```

## How the two stay identical

The asynchronous client is the source; the synchronous one is generated from it
by `scripts/unasync.py`, and CI fails if the generated copy is stale. Retry
rules, poll loops and paging logic therefore cannot diverge between them. The
same test suite runs against both.

## Concurrency

An `AsyncForwardClient` belongs to the event loop that created it. A synchronous
`ForwardClient` is safe to share across threads.

Either way, one client holds one connection pool, so share it rather than
creating one per task. Remember that Forward's hosted service enforces a
per-account request ceiling; see [configuration](configuration.md).
