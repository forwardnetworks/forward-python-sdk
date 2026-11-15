"""Typed path queries for Forward's flow-trace facets (``client.flow_trace``).

``get_trace_facets`` and its siblings take the request body Forward's own
Search page sends, whose ``filters`` is a PathQuery: where traffic enters the
network (``from_``), the hops it must pass (``chain``), where it leaves
(``to``), and header filters on each. This module builds that object and
refuses, before anything is sent, what Forward would refuse or silently
misread. It mirrors the Go SDK's ``TracePathQuery`` (v0.8.27).

PREVIEW, like the routes it feeds: Forward documents none of this and makes no
stability promise. What was seen live (Forward 26.11, a two-device lab) is
noted as such; the rest is read from Forward source.

    from forward_sdk.trace import Endpoint, PathQuery, device, interface

    query = PathQuery(
        from_=Endpoint(device("edge-1"), headers=[{"ipv4_dst": ["192.0.2.10"], "ip_proto": ["6"]}]),
        to=Endpoint(interface("core-1", "eth1/1")),
    )
    facets = client.flow_trace.get_trace_facets(body=query.request(max_results=100))
    total = facets.total_results  # None on a timeout or when possible_ip_locations is set

Notes from the live probe:

- An interface is named ``"<device> <interface>"`` with a space; the comma form
  Forward uses for port names elsewhere is a 400. ``interface()`` builds it.
- A ``subnet()`` in ``to`` returned no results even for an address inside the
  network, while an interface in ``to`` returned paths. The Go SDK reads the
  source as ``to`` meaning where traffic leaves the network (not confirmed).
  Put an in-network destination in ``from_`` headers (``ipv4_dst``) or name a
  device or interface in ``to``.
- No results is ``totalResults {"value": 0, "type": "EXACT"}``.
- ``ipv6_dst`` and ``mode=PERMIT_ALL`` were accepted; neither produced results
  to check in the lab.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover
    from forward_sdk._generated.models import (
        TraceFacetsResponse,
        TracePathFacet,
        TracePathGroup,
    )

__all__ = [
    "PERMIT_ALL",
    "ChainHop",
    "Endpoint",
    "Location",
    "PathQuery",
    "Transit",
    "device",
    "device_vrf",
    "host",
    "interface",
    "interface_values",
    "negate",
    "security_zone",
    "selection",
    "selections",
    "subnet",
    "subnet_at_device",
    "vrf",
]

#: Compute flows as if every ACL-drop rule permitted the traffic. Needs the org
#: property ACL_LESS_ANALYSIS (on by default); what Forward answers with it off
#: was not seen.
PERMIT_ALL = "PERMIT_ALL"

Transit = Literal["through", "ingress", "egress"]
_TRANSITS = ("through", "ingress", "egress")

#: Header fields Forward allows only in ``from_`` (source). Forward also limits
#: the layer-7 fields (url, domain, dns_*, http_method, ...) to ``from_``; those
#: are left to its 400.
_FROM_ONLY = frozenset({"app_id", "user_id", "user_group_id"})

_VALUES_KINDS = ("HostFilter", "DeviceFilter", "InterfaceFilter", "VrfFilter")


@dataclass(frozen=True)
class Location:
    """One location filter. Build it with :func:`device`, :func:`interface` and the others."""

    kind: str
    values: tuple[str, ...] = ()
    value: str | None = None
    device: str | None = None
    vrf: str | None = None
    clause: Location | None = None

    def check(self, *, hop: bool = False) -> None:
        """Raise ``ValueError`` for what Forward refuses.

        That is a blank name, or a host or subnet as a chain hop.
        """
        if self.kind in _VALUES_KINDS:
            if not self.values:
                raise ValueError(f"a {self.kind} needs at least one value")
            if any(not isinstance(v, str) for v in self.values):
                raise ValueError(f"a {self.kind} takes names as separate arguments, not a list")
            if any(not v.strip() for v in self.values):
                raise ValueError(f"a {self.kind} cannot contain a blank value")
        elif self.kind == "DeviceVrfFilter":
            if not (self.device or "").strip() or not (self.vrf or "").strip():
                raise ValueError("a device VRF location needs a device and a VRF")
        elif self.kind == "SecurityZoneFilter":
            if not (self.device or "").strip() or not (self.value or "").strip():
                raise ValueError("a security zone location needs a device and a zone")
        elif self.kind == "SubnetLocationFilter":
            if not (self.value or "").strip():
                raise ValueError("a subnet location needs an address or prefix")
        elif self.kind == "NotFilter":
            if self.clause is None:
                raise ValueError("a negated location needs a clause")
            if self.clause.kind == "NotFilter":
                raise ValueError("a negated location cannot contain another negation")
            self.clause.check(hop=hop)
            return
        else:
            raise ValueError(f"unknown location type {self.kind!r}")
        if hop and self.kind in ("HostFilter", "SubnetLocationFilter"):
            raise ValueError(f"a {self.kind} cannot be a hop in a chain")

    def to_api(self) -> dict[str, Any]:
        """The filter object Forward reads."""
        out: dict[str, Any] = {"type": self.kind}
        if self.kind in _VALUES_KINDS:
            out["values"] = list(self.values)
        elif self.kind == "DeviceVrfFilter":
            out["device"], out["vrf"] = self.device, self.vrf
        elif self.kind == "SecurityZoneFilter":
            out["device"], out["value"] = self.device, self.value
        elif self.kind == "SubnetLocationFilter":
            out["value"] = self.value
            if self.device:
                out["device"] = self.device
        elif self.kind == "NotFilter":
            if self.clause is None:
                raise ValueError("a negated location needs a clause")
            out["clause"] = self.clause.to_api()
        return out


def host(*names: str) -> Location:
    """Hosts by name (HostFilter). Not allowed as a chain hop. Source only."""
    return Location("HostFilter", values=tuple(names))


def device(*names: str) -> Location:
    """Devices by name (DeviceFilter). Allowed everywhere; seen live."""
    return Location("DeviceFilter", values=tuple(names))


def interface(device_name: str, interface_name: str) -> Location:
    """One interface of one device, sent as ``"<device> <interface>"`` (seen live).

    A blank part is refused when the query is built.
    """
    device_name, interface_name = device_name.strip(), interface_name.strip()
    if not device_name or not interface_name:
        return Location("InterfaceFilter", values=("",))
    return Location("InterfaceFilter", values=(f"{device_name} {interface_name}",))


def interface_values(*values: str) -> Location:
    """Interfaces by Forward's own ``"<device> <interface>"`` strings."""
    return Location("InterfaceFilter", values=tuple(values))


def vrf(*names: str) -> Location:
    """VRFs by name on any device (VrfFilter). Source only."""
    return Location("VrfFilter", values=tuple(names))


def device_vrf(device_name: str, vrf_name: str) -> Location:
    """One VRF of one device (DeviceVrfFilter). Source only."""
    return Location("DeviceVrfFilter", device=device_name, vrf=vrf_name)


def security_zone(device_name: str, zone: str) -> Location:
    """A security zone of one device (SecurityZoneFilter). Source only."""
    return Location("SecurityZoneFilter", device=device_name, value=zone)


def subnet(cidr: str) -> Location:
    """An address or prefix (SubnetLocationFilter). Not allowed as a chain hop.

    In ``to`` it returned nothing live even for an in-network address; see the
    module notes. Forward refuses loopback prefixes (source).
    """
    return Location("SubnetLocationFilter", value=cidr)


def subnet_at_device(cidr: str, device_name: str) -> Location:
    """An address or prefix resolved on one device, when it is in several places. Source only."""
    return Location("SubnetLocationFilter", value=cidr, device=device_name)


def negate(location: Location) -> Location:
    """Everything but ``location`` (NotFilter). Source only."""
    return Location("NotFilter", clause=location)


Headers = Mapping[str, Sequence[str]]


def _headers(items: Sequence[Headers], *, where: str, from_ok: bool) -> list[dict[str, Any]]:
    if isinstance(items, Mapping):
        raise ValueError(f"{where}: headers is a list of header filters; wrap the mapping in [...]")
    out = []
    for item in items:
        if not item:
            raise ValueError(f"{where}: a header filter needs at least one field")
        for name, values in item.items():
            if isinstance(values, str):
                raise ValueError(f"{where}: header {name} takes a list of values, not a string")
            if not name.strip() or not values:
                raise ValueError(f"{where}: a header filter field needs a name and a value")
            if any(not str(v).strip() for v in values):
                raise ValueError(f"{where}: header {name} has a blank value")
            if not from_ok and name in _FROM_ONLY:
                raise ValueError(f"{where}: header {name} is allowed only in from_")
        out.append(
            {"type": "PacketFilter", "values": {k: [str(v) for v in vs] for k, vs in item.items()}}
        )
    return out


@dataclass(frozen=True)
class Endpoint:
    """A ``from_`` or ``to`` clause: a location and optional header filters.

    Each header filter maps Forward's lower-case field names to the values they
    may take, for example ``{"ipv4_dst": ["192.0.2.0/24"], "tp_dst": ["443"]}``.
    """

    location: Location
    headers: Sequence[Headers] = ()
    logical_network: str | None = None

    def _wire(self, where: str, *, from_ok: bool) -> dict[str, Any]:
        try:
            self.location.check()
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        out: dict[str, Any] = {"location": self.location.to_api()}
        if self.logical_network:
            out["logicalNetwork"] = self.logical_network
        if self.headers:
            out["headers"] = _headers(self.headers, where=where, from_ok=from_ok)
        return out


@dataclass(frozen=True)
class ChainHop:
    """A hop the traffic must pass. Source only.

    ``through`` is anywhere along the path; ``ingress`` and ``egress`` enter or leave there.
    """

    transit: Transit
    location: Location
    headers: Sequence[Headers] = ()


@dataclass(frozen=True)
class PathQuery:
    """The Search page's query, as ``filters`` of a flow-trace request.

    At least one of ``from_``, ``to`` and ``chain`` is required (a mode alone is
    not a query). ``mode`` is ``None`` or :data:`PERMIT_ALL`. The SDK also
    refuses two things Forward itself accepts, as Go does: an empty query and an
    empty header filter.
    """

    from_: Endpoint | None = None
    to: Endpoint | None = None
    chain: Sequence[ChainHop] = field(default_factory=tuple)
    mode: str | None = None

    def to_api(self) -> dict[str, Any]:
        """The PathQuery object; raises ``ValueError`` for anything Forward would refuse."""
        if self.mode is not None and self.mode != PERMIT_ALL:
            raise ValueError(f"unknown trace mode {self.mode!r} (only {PERMIT_ALL})")
        if self.from_ is None and self.to is None and not self.chain:
            raise ValueError("a path query needs a from_, a to or a chain")
        out: dict[str, Any] = {}
        if self.from_ is not None:
            out["from"] = self.from_._wire("from_", from_ok=True)
        if self.to is not None:
            out["to"] = self.to._wire("to", from_ok=False)
        if self.chain:
            hops = []
            for i, hop in enumerate(self.chain):
                where = f"chain hop {i}"
                if hop.transit not in _TRANSITS:
                    raise ValueError(f"{where} needs a transit of through, ingress or egress")
                try:
                    hop.location.check(hop=True)
                except ValueError as exc:
                    raise ValueError(f"{where}: {exc}") from None
                item: dict[str, Any] = {
                    "transitType": hop.transit,
                    "location": hop.location.to_api(),
                }
                if hop.headers:
                    item["headers"] = _headers(hop.headers, where=where, from_ok=False)
                hops.append(item)
            out["chain"] = hops
        if self.mode is not None:
            out["mode"] = self.mode
        return out

    def trace_request(
        self,
        group: TracePathGroup,
        path: TracePathFacet,
        *,
        max_results: int | None = None,
        max_duration_seconds: int | None = None,
    ) -> dict[str, Any]:
        """A body for ``client.flow_trace.get_trace``: this query, with one path selected.

        ``group`` and ``path`` come from the facets answer to this same query
        (see :func:`selections`), so a query with a chain, a ``to`` location,
        headers or ``PERMIT_ALL`` can be traced hop by hop as well as counted.
        Mirrors the Go SDK's ``TraceFor`` (v0.8.38).
        """
        return self.request(
            max_results=max_results,
            max_duration_seconds=max_duration_seconds,
            **selection(group, path),
        )

    def request(
        self,
        *,
        max_results: int | None = None,
        max_duration_seconds: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """A whole request body for ``get_trace_facets`` (and the other trace routes).

        ``max_results`` caps the flows examined (1..10000, Forward's default
        5000); ``max_duration_seconds`` is the time budget (1..300, default 30).
        Anything else, such as ``selectedFacets`` for ``get_trace``, passes
        through unchanged.
        """
        for name, value, top in (
            ("max_results", max_results, 10000),
            ("max_duration_seconds", max_duration_seconds, 300),
        ):
            if value is not None and not 1 <= value <= top:
                raise ValueError(f"{name} must be 1..{top}, not {value}")
        body: dict[str, Any] = {"filters": self.to_api()}
        if max_results is not None:
            body["maxResults"] = max_results
        if max_duration_seconds is not None:
            body["maxDurationSeconds"] = max_duration_seconds
        body.update(extra)
        return body


def selection(group: TracePathGroup, path: TracePathFacet) -> dict[str, Any]:
    """The ``selectedFacets`` and ``limit`` that pick one path of a facets answer for ``get_trace``.

    The path's ``behaviorSample`` goes back as Forward sent it (left out when
    there is none), and ``limit`` is 100, as the GUI and the Go SDK send. A path
    with no hops or a group with no trace type is refused, as Go does, rather
    than left to fail on the server.
    """
    if not path.path:
        raise ValueError("the facets path has no hops to select")
    if not (group.trace_type or "").strip():
        raise ValueError("the facets path group has no trace type")
    chosen: dict[str, Any] = {"path": path.path, "traceType": group.trace_type}
    if path.behavior_sample is not None:
        chosen["behavior"] = path.behavior_sample.to_api()
    return {"selectedFacets": {"paths": [chosen]}, "limit": 100}


def selections(
    facets: TraceFacetsResponse,
) -> list[tuple[TracePathGroup, TracePathFacet]]:
    """Every (group, path) pair a facets answer lists, in Forward's order.

    Trace each with ``query.trace_request(group, path)``; that is the Go SDK's
    ``ExplainQuery`` as a loop, one request per path, so slice the list to cap
    it as Go's ``ExplainLimit`` does::

        facets = client.flow_trace.get_trace_facets(body=query.request())
        traces = [
            client.flow_trace.get_trace(body=query.trace_request(group, path))
            for group, path in selections(facets)
        ]
    """
    groups = facets.facets.path_groups if facets.facets is not None else None
    return [(group, path) for group in groups or [] for path in group.paths or []]
