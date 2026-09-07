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

## Telemetry

Each execution leaves a record on the client, so a job that runs many queries
across threads can report what happened without holding on to the handles:

```python
for report in client.nqe.execution_reports():
    print(
        report.query,
        report.rows_produced,
        report.millis_executing,
        report.poll_count,
        report.terminal_reason,
    )
```

`terminal_reason` is Forward's outcome when it finished, or the client-side
reason waiting stopped. Retention is bounded and thread-safe, and reading it
never raises, since the place it is usually read is a failure path.

## Diffing across snapshots

```python
changes = client.nqe.diff(QueryRef.by_id("FQ_..."), before="100", after="101")
for entry in changes:
    print(entry.type, entry.before, entry.after)
```

The snapshots are keyword-only because nothing in a pair of ids says which is
which. `before` and `after` are independent and either may be absent: a row
added between the snapshots has no `before`, a removed one has no `after`.

Only a committed query can be diffed, since Forward cannot diff source it has
never seen. Passing inline source raises `ForwardConfigurationError`.

## Pinning a query version

`commit_id` pins a query to one committed version. Two values are treated
specially:

- `"head"` is dropped, because Forward does not understand the symbolic name
  and omitting the field means the same thing.
- An abbreviated hash raises `ForwardConfigurationError`. Forward needs the full
  40 characters, and silently dropping the pin would run against whatever is at
  head and report success, which is exactly what pinning exists to prevent.

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

Two shapes, and picking the wrong one fails quietly:

| Field holds | Helper | Emits |
| --- | --- | --- |
| A collection, e.g. `device.tagNames` | `membership()` | `"core" in device.tagNames` |
| A string, e.g. `device.platform.model` | `one_of()` | `device.platform.model in ["C9300"]` |
| An enum, e.g. `device.platform.vendor` | `enum_one_of()` | `device.platform.vendor == Vendor.CISCO` |

All three verified against a live instance. The distinctions are not stylistic:

- `membership()` on a scalar matches nothing, so a probe built that way reports
  an empty scope on a healthy network and reads like a data problem.
- `one_of()` on an enum fails at run time with *the type of lookup value Vendor
  is not equal to list element type String*, because NQE compares by type and an
  enum member is not a string.

```python
from forward_sdk.models import Vendor
from forward_sdk.nqe.where import enum_one_of, membership, one_of

enum_one_of("device.platform.vendor", "Vendor", [Vendor.cisco, Vendor.arista])
one_of("device.platform.osVersion", ["17.9.4"])
membership("device.tagNames", ["production"])
```

Members may be shipped enum values, which removes any question of how a member
is spelled. Names are validated as identifiers rather than quoted, since quoting
one turns it into a string and reintroduces the type error.

!!! warning "The NQE type name is not the SDK class name"

    NQE's data model is its own namespace and the two disagree. Verified
    against a live instance:

    | Field | NQE type | SDK model class |
    | --- | --- | --- |
    | `device.platform.vendor` | `Vendor` | `Vendor` |
    | `device.platform.deviceType` | `DeviceType` | `DeviceType` |
    | `device.platform.os` | `OS` | `VendorOs` |

    Passing `VendorOs` fails with *Variable VendorOs not in scope*. Read the
    type name off a `toString()` rendering of the field, which prefixes it:
    `toString(device.platform.os)` gives `"OS.PAN_OS"`.

    The same prefix matters when reading values back. A query selecting
    `toString(device.platform.os)` yields `OS.PAN_OS`, not `PAN_OS`, so strip
    the prefix if you want the bare member.

Enum members are validated as identifiers rather than quoted, since quoting one
would make it a string and reintroduce the type error.
