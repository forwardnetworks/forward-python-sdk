# NQE

The Network Query Engine is how most integrations read the Forward model. Rather
than fetching typed resources, you run a query and consume rows.

## Two ways to run a query

`run()` sends the query and waits for the result in one request. Simple, and
right for small or interactive queries.

```python
result = client.nqe.run(
    "foreach d in network.devices select {name: d.name}",
    network_id="101",
    limit=1000,
)
result.items  # list[dict[str, Any]]
result.total_num_items  # how many rows the query produced in total
```

`execute()` starts the query in the background and returns a handle. This is
what long-running or large queries need, and the only path that can stream.

```python
execution = client.nqe.execute("foreach d in network.devices select {n: d.name}")
execution.wait()
for row in execution.rows():
    ...
```

`query()` wraps the second for the common case of "run this and give me every
row":

```python
rows = client.nqe.query(query_ref, network_id="101")
```

## Naming a query

`QueryRef` covers the three mutually exclusive ways to identify a query:

```python
from forward_sdk import QueryRef

QueryRef.inline("foreach d in network.devices select {n: d.name}")
QueryRef.by_id("FQ_ac651cb2901b067fe7dbfb511613ab44776d8029")
QueryRef.by_path("/NetBox/Devices")
```

A path is resolved to an ID before the query runs, through an index cached for
the client's lifetime, so resolving many paths does not refetch the library each
time. Pin a version with `commit_id`, and pass query parameters as keywords:

```python
QueryRef.by_path("/NetBox/Devices", commit_id=commit, site="nyc")
```

## Paging or streaming

`rows()` fetches a page at a time. Each page is its own request, so a transient
failure is retried.

```python
for row in execution.rows(page_size=10_000):
    ...
```

`stream()` reads the whole result as newline-delimited JSON. Rows arrive as they
are produced, which is faster and uses less memory, but a dropped connection
cannot be resumed.

```python
for row in execution.stream():
    ...
```

Choose `rows()` when the run must survive a blip, and `stream()` when the result
set is large and the run is short-lived.

### Guard rails

Paging stops itself rather than hanging or exhausting memory:

```python
from forward_sdk import PageGuards

rows = client.nqe.query(
    ref,
    guards=PageGuards(max_rows=500_000, max_pages=1000, repeat_limit=25),
)
```

Each limit raises `ForwardPaginationError` with what was collected so far. The
guards also catch two server-side faults that would otherwise be invisible: a
result set that ends before the total Forward promised, and a server that keeps
returning the same full page without advancing.

## Snapshots

Every query takes an optional `snapshot_id`. Omitting it, or passing `None`,
runs against the network's latest processed snapshot without an extra lookup.

## Diffing across snapshots

```python
changes = client.nqe.diff("100", "101", QueryRef.by_id("FQ_..."))
for entry in changes:
    print(entry["type"], entry["before"], entry["after"])
```

Only a committed query can be diffed, since Forward cannot diff source it has
never seen. Passing inline source raises `ForwardConfigurationError`.

## Query files

Queries shipped alongside your code usually need small transformations, which
`forward_sdk.nqe.files` provides:

```python
from forward_sdk.nqe.files import load_query, select_field_sets, missing_fields

source = load_query("queries/devices.nqe")  # ready to run
published = load_query("queries/devices.nqe", for_execution=False)
```

`load_query` strips the `@primaryKey` annotation, which the run endpoints
reject, and inlines local `import "..."` lines, which the query library does not
support. `select_field_sets` and `missing_fields` read the columns a query
produces, which makes contract drift between a query and its consumer a test
failure rather than a production surprise.

## Building predicates

Scoping a query at runtime means assembling query text from values, so escape
them:

```python
from forward_sdk.nqe.where import tag_scope, literal

scope = tag_scope(include=["core", "edge"], exclude=["lab"])
query = f"foreach d in network.devices\n{scope}\nselect {{n: d.name}}"
```

`literal()` renders a Python value as an NQE literal with correct escaping.
Never interpolate a raw value into query text.
