# forward-sdk

Official Python SDK for the [Forward Networks](https://www.forwardnetworks.com/)
REST API.

> Status: pre-release. The public API is not yet stable.

```bash
pip install forward-sdk
```

```python
from forward_sdk import ForwardClient

with ForwardClient.from_env() as client:
    for network in client.networks.list():
        print(network.id, network.name)

    rows = client.nqe.query(
        "foreach device in network.devices select {name: device.name}",
        network_id="101",
    )
```

Authentication is HTTP basic with an API token: its access key is the username
and its secret is the password.

## What it covers

Every operation in Forward's API. The groups integrations use most -- NQE,
snapshots, networks, devices and device tags -- are hand-written with curated
names and behaviour such as waiting for a snapshot to finish processing. The
rest are generated from the OpenAPI description Forward produces from its own
server code.

Both a synchronous and an asynchronous client, with identical surfaces.

## Notable

**NQE first.** Running a query, paging or streaming the results, diffing a query
across snapshots, and publishing queries to the library are all first-class.
Paging carries guard rails for the ways it can go wrong in production: a result
set that ends early, a server that stops advancing, a query far larger than
expected.

**Typed, but tolerant.** Responses are pydantic models. Unknown fields and
unknown enum values are preserved rather than rejected, so a Forward release
that adds either does not break an older SDK. NQE rows stay `dict[str, Any]`,
because a query's columns are defined by the query.

**Honest about refusals.** A missing licence, a feature absent from your
deployment, and a role-based denial all arrive as the same status codes, so the
SDK does not pretend to tell them apart. It gives you Forward's own explanation
and a documented hint about what gates that operation.

## Documentation

<https://forwardnetworks.github.io/forward-python-sdk/>

- [Quickstart](docs/quickstart.md)
- [Configuration](docs/configuration.md)
- [NQE guide](docs/nqe/index.md)
- [Availability and gating](docs/gating.md)
- [Migrating from the NetBox plugin](docs/migration/netbox.md) or
  [the Nautobot plugin](docs/migration/nautobot.md)

## Requirements

Python 3.10 or newer.

## Licence

MIT.
