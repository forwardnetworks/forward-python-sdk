# Flow trace queries

`client.flow_trace` exposes the routes Forward's own Search page uses to trace
traffic: the distinct paths a query takes (`get_trace_facets`), one path in
detail (`get_trace`), return traffic, behaviours, flows and diffs. Forward
documents none of them and makes no stability promise.

The facets, trace, behaviour and flow routes take a body that carries a path query.
Build it with `forward_sdk.trace` rather than by hand: the builder writes the shapes
Forward expects and refuses many mistakes before sending. It does not catch
everything; see [What goes where](#what-goes-where).

Return traffic (`get_trace_responses`) and diffs (`get_trace_diffs`) take no path
query. Their body is `{"details": ...}`, plus optional `maxResults` and
`maxDurationSeconds`, with `details` copied unchanged from a `get_trace` result:
`resp_trace_req` for return traffic, or `trace_diff_req` from the before snapshot for
diffs. `get_trace_diffs` takes `before_snapshot_id` and `after_snapshot_id`, and no
`network_id`.

```python
from forward_sdk.trace import ChainHop, Endpoint, PathQuery, device, interface

query = PathQuery(
    from_=Endpoint(device("edge-1"), headers=[{"ipv4_dst": ["192.0.2.10"], "ip_proto": ["6"]}]),
    chain=[ChainHop("through", device("fw-1"))],
    to=Endpoint(interface("core-1", "eth1/1")),
)
snapshot_id = client.snapshots.latest_processed_id()  # pin one snapshot
facets = client.flow_trace.get_trace_facets(
    body=query.request(max_results=100), snapshot_id=snapshot_id
)

if facets.possible_ip_locations:
    ...  # an address is in several places: name one with subnet_at_device() and ask again
elif facets.total_results is None:
    ...  # timed out: the paths are partial
else:
    print(facets.total_results.value, facets.total_results.type)
```

## Tracing each path

The facets list the distinct paths; `get_trace` gives one of them hop by hop.
`selections()` lists every path of a facets answer, and `trace_request()`
builds the trace body for one, with the same query:

```python
from forward_sdk.trace import selections

traces = [
    client.flow_trace.get_trace(body=query.trace_request(group, path), snapshot_id=snapshot_id)
    for group, path in selections(facets)[:20]  # one request per path: cap it
]
```

Pass the same `snapshot_id` to both calls. A call without one uses the client's
configured snapshot or, if there is none, the latest processed snapshot, which can
change between calls. A trace of only `{timedOut}` means no flow was found for that
path. Seen live on a hosted Forward instance (2026-11-18): a query with a `through`
chain hop listed valid, dropped, blackholed and unreachable paths, and `trace_request`
traced each selected path hop by hop, its hop count equal to the path's length.

## What goes where

- `from_` is where traffic enters the network and `chain` the hops it must
  pass. A subnet in `to` returned no paths live, even for an address inside the
  network, while an interface in `to` did. The Go SDK reads Forward's source as
  `to` meaning where traffic leaves the network; that reading is not
  confirmed. Put an in-network destination in the `from_` headers
  (`ipv4_dst`), or name a device or interface in `to`.
- Not everything Forward refuses is caught first: `subnet()` accepts a loopback
  prefix, and `interface_values()` accepts any non-blank string, including the comma
  form above. Nor does it stop a subnet in `to`, which returned no paths live.
- The SDK refuses an empty query and an empty header filter, which Forward
  itself accepts. The Go SDK refuses them too.
- An interface is `"<device> <interface>"` with a space. Forward refuses the
  comma form it uses for port names elsewhere. `interface()` builds it.
- A host or subnet cannot be a chain hop.
- `app_id`, `user_id`, `user_group_id` and the layer-7 header fields are
  allowed only in `from_`. The SDK refuses the first three itself and leaves
  the layer-7 names to Forward's 400.
- `mode=PERMIT_ALL` asks Forward to trace as if ACL drops permitted the traffic
  (its effect on results was not verified live). It needs
  the org property ACL_LESS_ANALYSIS, which is on by default.

## What was seen live

On a Forward 26.11 two-device lab: device, interface and subnet locations, the
interface naming, an empty result as `{"value": 0, "type": "EXACT"}`, and
`ipv6_dst` and `PERMIT_ALL` accepted, though neither produced results to check.

Seen on 26.11 on 2026-11-15: a device chain hop with `through` narrows the paths and
`egress` is accepted (a persistent Existential check also took a device hop written
with the singular `value` as well as `values`, which the SDK sends); header filters
in `from_` (`ip_proto`, `tp_dst`, an `ipv4_dst` prefix) narrow; a device in `to`
returns the paths leaving there; and `get_trace` with the same filters traces a
searched path hop by hop.

The 2026-11-18 observations are under [Tracing each path](#tracing-each-path). The
reference below marks `ChainHop` as source only; these chain-hop observations are
not reflected there. They do not cover `ingress`.

Behaviour this page does not report as seen live, including the other location types
and `possibleIpLocations`, is read from Forward source. Not confirmed: tracing to the
internet, IPv6 results, and what Forward answers to `PERMIT_ALL` with
ACL_LESS_ANALYSIS off.

## Reference

::: forward_sdk.trace

## Source

- `src/forward_sdk/trace.py`
- `src/forward_sdk/_async/services/_generated/flow_trace.py`
- `src/forward_sdk/_sync/services/_generated/flow_trace.py`
- `src/forward_sdk/_generated/models.py`
