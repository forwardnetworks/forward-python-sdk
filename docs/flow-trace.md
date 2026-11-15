# Flow trace queries

`client.flow_trace` exposes the routes Forward's own Search page uses to trace
traffic: the distinct paths a query takes (`get_trace_facets`), one path in
detail (`get_trace`), return traffic, behaviours, flows and diffs. Forward
documents none of them and makes no stability promise.

Their request body carries a path query. Build it with `forward_sdk.trace`
rather than by hand: the builder writes the shapes Forward expects and refuses,
before sending, what Forward would refuse or quietly misread.

```python
from forward_sdk.trace import ChainHop, Endpoint, PathQuery, device, interface

query = PathQuery(
    from_=Endpoint(device("edge-1"), headers=[{"ipv4_dst": ["192.0.2.10"], "ip_proto": ["6"]}]),
    chain=[ChainHop("through", device("fw-1"))],
    to=Endpoint(interface("core-1", "eth1/1")),
)
facets = client.flow_trace.get_trace_facets(body=query.request(max_results=100))

if facets.possible_ip_locations:
    ...  # an address is in several places: name one with subnet_at_device() and ask again
elif facets.total_results is None:
    ...  # timed out: the paths are partial
else:
    print(facets.total_results.value, facets.total_results.type)
```

## What goes where

- `from_` is where traffic enters the network and `chain` the hops it must
  pass. A subnet in `to` returned no paths live, even for an address inside the
  network, while an interface in `to` did. The Go SDK reads Forward's source as
  `to` meaning where traffic leaves the network; that reading is not
  confirmed. Put an in-network destination in the `from_` headers
  (`ipv4_dst`), or name a device or interface in `to`.
- The SDK refuses an empty query and an empty header filter, which Forward
  itself accepts, as the Go SDK does.
- An interface is `"<device> <interface>"` with a space. Forward refuses the
  comma form it uses for port names elsewhere. `interface()` builds it.
- A host or subnet cannot be a chain hop.
- `app_id`, `user_id`, `user_group_id` and the layer-7 header fields are
  allowed only in `from_`. The SDK refuses the first three itself and leaves
  the layer-7 names to Forward's 400.
- `mode=PERMIT_ALL` traces as if ACL drops permitted the traffic. It needs
  the org property ACL_LESS_ANALYSIS, which is on by default.

## What was seen live

Forward 26.11, a two-device lab: device, interface and subnet locations, the
interface naming, an empty result as `{"value": 0, "type": "EXACT"}`, and
`ipv6_dst` and `PERMIT_ALL` accepted. Everything else here, including chain
hops, the other location types and `possibleIpLocations`, is read from
Forward source. Not confirmed: tracing to the internet, IPv6 results, and what
Forward answers to `PERMIT_ALL` with ACL_LESS_ANALYSIS off.

## Reference

::: forward_sdk.trace
