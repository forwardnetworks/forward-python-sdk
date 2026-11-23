# Migrating from the Nautobot SSoT plugin

`nautobot-app-ssot-forward` carries its own `ForwardClient` in
`forward_nautobot/integrations/forward/client.py`. Its structure is close to
this SDK's, so the migration is mostly renaming.

## Method mapping

| Plugin method | SDK |
| --- | --- |
| `get_networks()` | `client.networks.list()` |
| `get_snapshots()` | `client.snapshots.list()` |
| `get_latest_processed_snapshot(_id)()` | `client.snapshots.latest_processed(_id)()` |
| `resolve_snapshot_id()` | Pass `snapshot_id=None` for the latest processed |
| `get_snapshot_metrics()` | `client.snapshots.metrics()` |
| `run_nqe_query()` / `run_nqe_query_async()` | `client.nqe.query()` |
| `request_nqe_execution()` | `client.nqe.execute()` |
| `get_nqe_execution_result()` | `execution.rows()` |
| `_fetch_ndjson_stream()` | `execution.stream()` |
| `run_nqe_diff()` | `client.nqe.diff()` |
| `get_nqe_repository_query_index()` | `client.nqe.repo.index()` |
| `get_committed_nqe_query()` | `client.nqe.repo.queries(path=..., with_source=True)` |
| `get_org_nqe_draft_changes()` | `client.nqe.repo.drafts()` |
| `discard_org_nqe_draft_change()` | `client.nqe.repo.discard()` |
| `dry_run_org_nqe_queries()` | `client.nqe.repo.dry_run()` |
| `commit_org_nqe_queries()` | `client.nqe.repo.commit()` |
| `publish_bundled_queries()` | `client.nqe.repo.publish()` |
| `ForwardQuerySpec` | `forward_sdk.QueryRef` |
| `read_bundled_query_execution_source()` | `forward_sdk.nqe.files.load_query()` (for publishing, pass `for_execution=False` to keep `@primaryKey`) |
| `get_query_contract_field_sets()` | `forward_sdk.nqe.files.select_field_sets()` |
| `ForwardConnectionSettings` | `ForwardClient(...)` arguments |

## Naming

`ForwardQuerySpec` becomes `QueryRef`, with the same "exactly one of
source, id or path" rule. The source field is named `text` (and `query_id`,
`path`); `QueryRef.inline(source)` takes it positionally. Commit ids are
validated: `"head"` is dropped, and a hash shorter than 40 characters raises
`ForwardConfigurationError`, so a caller that pinned an abbreviated hash now
fails instead of running against head.

```python
from forward_sdk import QueryRef

QueryRef.inline(source)
QueryRef.by_id(query_id, commit_id=commit_id)
QueryRef.by_path("/forward_nautobot_validation/forward_devices")
```

A path reference is not runnable until it is resolved to a query id;
`client.nqe.run()` and `query()` do that for you, but `to_payload()` on an
unresolved path raises `ForwardConfigurationError`. `by_path` also takes
`repository="org"` (the default) or `"fwd"`.

`sort_keys` moves onto the reference:

```python
QueryRef.by_id("FQ_...").with_sort("device", "name")
```

Each string is one ascending sort key, so this sorts by `device`, then `name`.
For descending order pass `SortKey(column_name, order="DESC")` from
`forward_sdk.nqe.query_ref`.

## Behaviour you gain

- The streaming path is retried while connecting. The plugin's `_fetch_ndjson_stream`
  bypasses its retry loop entirely, so a refused connection fails the whole sync.
- Paging carries a row ceiling and identical-page detection, not just a page
  ceiling.
- `client.nqe.run()` and `execute()` take `column_filters` as well as sort keys;
  the plugin has only the latter.
- Explicit proxy support, rather than relying on `trust_env` alone.

## Exceptions

`ForwardClientError` maps to `ForwardAPIError` and `ForwardTransportError`, and
`ForwardConfigurationError` keeps its name and meaning. The query-file helpers in
`forward_sdk.nqe.files` (`load_query`, `inline_local_imports`,
`local_import_closure`) raise a plain `ValueError` on an import cycle, a missing
imported file, or a path that escapes `root`.

## The cloud path

The plugin's cloud feature reads everything through its bundled `.nqe` query
files, so it makes only NQE calls. The plugin needs no cloud-account endpoints, so it
uses nothing of the SDK's `client.cloud_accounts`.

## Tests

`tests/test_client.py` injects `httpx.MockTransport` through a `transport=`
field, and the SDK takes the same argument. Do not expect the port to be cheap
on that basis: it is around 1300 lines of wire-format conformance tests
asserting exact paths, query parameters, JSON bodies and Accept headers, and it
monkeypatches `time.sleep`, `time.monotonic` and `httpx.Client` on the client
module. The transport seam carries the shape across; the assertions get
rewritten.

## Source

- `src/forward_sdk/nqe/query_ref.py`
- `src/forward_sdk/nqe/files.py`
