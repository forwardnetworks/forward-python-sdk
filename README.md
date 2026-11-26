# forward-sdk

Python SDK for the [Forward Networks](https://www.forwardnetworks.com/) REST API.

> **A Forward Networks Field Integration.** Built and maintained by the field
> team to make integration work easier. It is not a supported Forward Networks
> product: it carries no SLA, and issues are handled on a best-effort basis
> rather than through Forward Support.
>
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

`from_env` reads `FORWARD_URL`, `FORWARD_USERNAME`, `FORWARD_PASSWORD` and the
other variables listed under
[Configuration](https://forwardnetworks.github.io/forward-python-sdk/configuration/).

Authentication is HTTP basic. Use an API token: its access key is the username
and its secret is the password. A login name and password also work, but are
subject to two-factor authentication and account lockout.

## What it covers

Every operation in Forward's API. Some groups, among them NQE, snapshots,
networks, devices and device tags, are hand-written with curated names and
behaviour such as waiting for a snapshot to finish processing. Most of the others
are generated from the OpenAPI description Forward produces from its own server
code.

Both a synchronous and an asynchronous client, with the same surface except that
you close the asynchronous one with `aclose()` rather than `close()`. An
asynchronous client belongs to the event loop that created it.

The client also has `call(operation_id, ...)`, for an operation chosen at run
time (streaming operations are refused); `access()`, which reports what your
account may do from its roles, read through an unpublished endpoint and cached
until `refresh=True`; `version()`; and request `counters`.

By default, requests to Forward's hosted service are paced
(`rate_limit_rpm="auto"`) and requests to self-hosted deployments are not. Where
`snapshot_id` is optional and neither the call nor the client sets one, Forward
uses the network's latest processed snapshot.

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

- [Quickstart](https://forwardnetworks.github.io/forward-python-sdk/quickstart/)
- [Configuration](https://forwardnetworks.github.io/forward-python-sdk/configuration/)
- [NQE guide](https://forwardnetworks.github.io/forward-python-sdk/nqe/)
- [Availability and gating](https://forwardnetworks.github.io/forward-python-sdk/gating/)
- [Migrating from the NetBox plugin](https://forwardnetworks.github.io/forward-python-sdk/migration/netbox/) or
  [the Nautobot plugin](https://forwardnetworks.github.io/forward-python-sdk/migration/nautobot/)

## Requirements

Python 3.10 or newer.

## Licence

Fair Core License 1.0 with an Apache 2.0 future licence (FCL-1.0-ALv2, see
[LICENSE](https://github.com/forwardnetworks/forward-python-sdk/blob/main/LICENSE)). It is source-available, not open source: you can use,
modify and redistribute the SDK for any purpose except a competing commercial
product or service. You may not move, change, disable or circumvent license key
functionality, or modify code the license key protects to use the protected
functionality without a valid key or to remove that functionality. Each release
becomes available under the Apache License 2.0 two years after it is published. Releases up to and including
0.1.41 remain available under the MIT License. See [NOTICE](https://github.com/forwardnetworks/forward-python-sdk/blob/main/NOTICE).

## Source

- `src/forward_sdk/__init__.py`
- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/_async/client.py`
- `src/forward_sdk/config.py`
- `pyproject.toml`
- `LICENSE`
- `NOTICE`
