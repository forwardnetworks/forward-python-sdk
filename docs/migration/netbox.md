# Migrating from forward-netbox

The Forward NetBox plugin carries its own `ForwardClient` in
`forward_netbox/utilities/forward_api_impl.py`. This SDK is that client's
successor, and the plugin can become a thin adapter over it.

## Method mapping

| Plugin method | SDK |
| --- | --- |
| `get_networks()` | `client.networks.list()` |
| `get_snapshots(network_id, include_archived=, limit=)` | `client.snapshots.list(network_id, include_archived=, limit=)` |
| `get_latest_processed_snapshot(_id)()` | `client.snapshots.latest_processed(_id)()` |
| `get_latest_collected_snapshot_id()` | `client.snapshots.latest_collected_id()` |
| `get_snapshot_metrics()` | `client.snapshots.metrics()` |
| `trigger_snapshot_reachability()` | `client.snapshots.start_reachability_job()` |
| `run_nqe_query(..., fetch_all=True)` | `client.nqe.query(...)` |
| `run_nqe_query(..., fetch_all=False)` | `client.nqe.run(...)` |
| `run_nqe_diff()` | `client.nqe.diff()` |
| `get_org_nqe_queries()`, `get_nqe_repository_queries()` | `client.nqe.repo.queries()` |
| `get_committed_nqe_query()` | `client.nqe.repo.queries(path=..., with_source=True)` |
| `get_nqe_query_history()` | `client.nqe.repo.history()` |
| `get_org_nqe_head_commit_id()` | `client.nqe.repo.head_commit_id()` |
| `add_org_nqe_query()` | `client.nqe.repo.stage_add()` |
| `edit_org_nqe_query()` | `client.nqe.repo.stage_edit()` |
| `commit_org_nqe_queries()` | `client.nqe.repo.commit()` or `publish()` |
| `get_device_mgmt_tags()` | `client.device_tags.list(with_devices=True)`, or an NQE query |
| `build_device_tag_scope_where()` | `forward_sdk.nqe.where.tag_scope()` |
| `QuerySpec` | `forward_sdk.QueryRef` |
| `_compile_query_file()` | `forward_sdk.nqe.files.load_query()` |

## What the SDK already handles

Several behaviours the plugin implements by hand are built in, so the adapter
can drop them:

- Retry classification, `Retry-After` in both forms, jittered backoff.
- The abbreviated-commit-id and `"head"` sanitizing before an execution.
- Paging guard rails: identical-page detection, early-end detection, and the
  page and row ceilings.
- The 409 `INVALID_CHANGE_PATH` "no changes at the following paths" retry.
- Request counters.

Two differences are worth knowing. The SDK keeps one `httpx` client for its
lifetime rather than creating one per request, which removes a TLS handshake
from every call. And its streaming path retries while connecting, which the
plugin's does not.

## Wiring plugin-specific behaviour

The plugin's Django integrations plug into the SDK's extension points rather
than requiring a fork:

```python
from forward_sdk import ForwardClient

client = ForwardClient(
    source.url,
    username=source.username,
    password=decrypt_secret(source.password),
    verify=source.parameters.get("verify", True),
    # The plugin's Django-cache limiter, shared across worker processes.
    throttle=DjangoCacheThrottle(source),
    # NetBox's proxy resolution.
    proxy=resolve_proxies(...),
    hooks=PluginTelemetryHooks(),
    user_agent=f"forward-netbox/{__version__}",
)
```

Anything with an `acquire()` method works as `throttle=`, which is what a
cross-process rate limit needs.

## Exceptions

`ForwardClientError` maps to `ForwardAPIError` for a response Forward returned,
and `ForwardTransportError` for a request that never got one.
`ForwardFetchBudgetExceededError` maps to `ForwardTimeoutError`, and
`ForwardQueryError` to `ForwardNqeQueryError`. Aliases are enough for one
release:

```python
from forward_sdk import ForwardAPIError, ForwardTransportError

ForwardClientError = (ForwardAPIError, ForwardTransportError)
```

## Tests

The plugin's tests patch `httpx.Client`, and the SDK accepts `transport=`, so
`httpx.MockTransport` replaces the patching. That understates the work, though:
`tests/test_forward_api.py` asserts against the private `_request` seam and on
the exact `httpx.Client(...)` keyword arguments, including `mounts=`, auth
tuples and Accept headers. Those assertions do not survive the swap and get
rewritten against the SDK's own surface.
