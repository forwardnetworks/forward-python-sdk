# forward-sdk

Python SDK for the [Forward Networks](https://www.forwardnetworks.com/) REST API.

!!! info "A Forward Networks Field Integration"

    Built and maintained by the field team to make integration work easier. It
    is not a supported Forward Networks product: it carries no SLA, and issues
    are handled on a best-effort basis rather than through Forward Support.

    It builds on Forward's published API, and on a few endpoints outside it that
    are [documented here](unpublished.md).

```python
from forward_sdk import ForwardClient

with ForwardClient.from_env() as client:
    for network in client.networks.list():
        print(network.id, network.name)
```

`from_env` needs `FORWARD_URL`, and `FORWARD_USERNAME` and `FORWARD_PASSWORD` to
authenticate. Authentication is HTTP basic, preferably with an API token. The
[quickstart](quickstart.md) shows how to get one, and
[configuration](configuration.md) covers the other variables and client settings:
TLS, proxies, timeouts, retries, rate limiting and caching.

## What it covers

The whole published API, plus a few endpoints Forward does not publish that
integrations depend on. The groups people use most are hand-written:

- **NQE**, the Network Query Engine, which is how most integrations read the
  Forward model. Run queries inline or in the background, page or stream the
  results, diff a query across two snapshots, and publish queries to the
  library. See [the NQE guide](nqe/index.md).
- **Snapshots**: list, inspect, upload, export, and wait for processing.
- **Networks**, **devices** and **device tags**.
- **AI**, **MCP**, **change reports**, **snapshot diffs**, **change sets**,
  **user events**, **TAPI network containers** and **client software**.

Everything else -- path search, checks, locations, topology, credentials,
collectors, VPNs, vulnerabilities and the rest -- is generated from Forward's
API description. See [the API groups](services.md).

Both a synchronous and an asynchronous client, with the same surface apart from
`close()` and `aclose()`. The synchronous one is generated from the asynchronous
one (see [asynchronous use](async.md)).

The client itself also offers `access()`, to check what your account's roles
allow (see [access](access.md)), and `call(operation_id, ...)`, to run an
operation chosen at run time, except a streaming one. SDK errors such as refused
requests and timeouts are described under [errors](errors.md).

## Design notes

**Generated from Forward's own description.** Models and the operation table are
generated from the OpenAPI description Forward produces from its server code, so
they cannot describe an API the server does not implement. A test proves every
operation has exactly one implementation.

**Typed, but tolerant.** Responses are pydantic models with typed fields.
Unknown fields and unknown enum values are preserved rather than rejected, so a
Forward release that adds either does not break an older SDK.

**NQE rows stay dictionaries.** A query's columns are defined by the query, and
column names such as `Interface Speed` are not valid Python identifiers, so rows
are `dict[str, Any]`.

## Installing

```bash
pip install forward-sdk
```

Requires Python 3.10 or newer. The package is pre-release (alpha), under the
Fair Core License (FCL-1.0-ALv2).

## Source

- `src/forward_sdk/__init__.py`
- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/_async/client.py`
- `pyproject.toml`
