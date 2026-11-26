# Asynchronous use

`AsyncForwardClient` mirrors `ForwardClient`: same constructor arguments, same
services, same method names, with one exception. Outside `async with`, close the
asynchronous client with `await client.aclose()`; the synchronous one has
`client.close()`.

Two argument types differ too. A custom `transport=` is an
`httpx.AsyncBaseTransport` here and an `httpx.BaseTransport` on the synchronous
client. A custom `throttle=` needs an `async def acquire()` here and a plain
`def acquire()` there.

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
by `scripts/unasync.py`. CI runs `scripts/check_generated.py`, which re-runs the
code generators, this one included, and fails if the committed output is stale.
The translation is mechanical text substitution, so logic that cannot be
translated that way belongs in the shared layers, not in `_async`. Retry rules,
poll loops and paging logic therefore cannot diverge between them. The tests in
`tests/_sync` are generated from `tests/_async` in the same way.

## Concurrency

An `AsyncForwardClient` belongs to the event loop that created it. A synchronous
`ForwardClient` is safe to share across threads (see
[Configuration](configuration.md#threads-and-event-loops) for the exceptions).

Either way, one client holds one connection pool, so share it rather than
creating one per task. Requests are paced by default against Forward's hosted
service (`rate_limit_rpm="auto"`), which enforces a per-account request
ceiling; see [configuration](configuration.md).

## Source

- `src/forward_sdk/_async/client.py`
- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/_async/throttle.py`
- `src/forward_sdk/_sync/throttle.py`
- `scripts/unasync.py`
- `scripts/check_generated.py`
