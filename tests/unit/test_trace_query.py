"""The typed path query sends what Forward's Search page sends and refuses what Forward refuses."""

from __future__ import annotations

import pytest

from forward_sdk.trace import (
    PERMIT_ALL,
    ChainHop,
    Endpoint,
    PathQuery,
    device,
    device_vrf,
    host,
    interface,
    negate,
    security_zone,
    subnet,
    subnet_at_device,
    vrf,
)


def test_a_full_query_has_the_wire_shape() -> None:
    query = PathQuery(
        from_=Endpoint(
            subnet("192.0.2.0/24"), headers=[{"ipv4_dst": ["198.51.100.10"], "ip_proto": ["6"]}]
        ),
        chain=[ChainHop("through", device("fw-1"))],
        to=Endpoint(interface("core-1", "eth1/1")),
        mode=PERMIT_ALL,
    )

    assert query.to_api() == {
        "from": {
            "location": {"type": "SubnetLocationFilter", "value": "192.0.2.0/24"},
            "headers": [
                {
                    "type": "PacketFilter",
                    "values": {"ipv4_dst": ["198.51.100.10"], "ip_proto": ["6"]},
                }
            ],
        },
        "to": {"location": {"type": "InterfaceFilter", "values": ["core-1 eth1/1"]}},
        "chain": [
            {"transitType": "through", "location": {"type": "DeviceFilter", "values": ["fw-1"]}}
        ],
        "mode": "PERMIT_ALL",
    }


def test_an_interface_is_named_with_a_space_not_a_comma() -> None:
    assert interface(" n1 ", "eth1/1").to_api() == {
        "type": "InterfaceFilter",
        "values": ["n1 eth1/1"],
    }


@pytest.mark.parametrize(
    ("location", "wire"),
    [
        (host("h1"), {"type": "HostFilter", "values": ["h1"]}),
        (vrf("blue"), {"type": "VrfFilter", "values": ["blue"]}),
        (device_vrf("r1", "blue"), {"type": "DeviceVrfFilter", "device": "r1", "vrf": "blue"}),
        (
            security_zone("fw-1", "trust"),
            {"type": "SecurityZoneFilter", "device": "fw-1", "value": "trust"},
        ),
        (
            subnet_at_device("192.0.2.1", "r1"),
            {"type": "SubnetLocationFilter", "value": "192.0.2.1", "device": "r1"},
        ),
        (
            negate(device("r1")),
            {"type": "NotFilter", "clause": {"type": "DeviceFilter", "values": ["r1"]}},
        ),
    ],
)
def test_each_location_kind_renders_its_fields(location, wire) -> None:  # type: ignore[no-untyped-def]
    assert location.to_api() == wire


def test_request_wraps_the_filters_with_limits() -> None:
    body = PathQuery(from_=Endpoint(device("r1"))).request(max_results=100, max_duration_seconds=60)

    assert body == {
        "filters": {"from": {"location": {"type": "DeviceFilter", "values": ["r1"]}}},
        "maxResults": 100,
        "maxDurationSeconds": 60,
    }


@pytest.mark.parametrize(
    ("query", "message"),
    [
        (PathQuery(), "needs a from_, a to or a chain"),
        (PathQuery(mode=PERMIT_ALL), "needs a from_, a to or a chain"),
        (PathQuery(from_=Endpoint(device("r1")), mode="DENY_ALL"), "unknown trace mode"),
        (PathQuery(chain=[ChainHop("through", host("h1"))]), "cannot be a hop"),
        (PathQuery(chain=[ChainHop("through", subnet("192.0.2.0/24"))]), "cannot be a hop"),
        (PathQuery(chain=[ChainHop("around", device("r1"))]), "through, ingress or egress"),  # type: ignore[arg-type]
        (PathQuery(to=Endpoint(negate(negate(device("r1"))))), "another negation"),
        (PathQuery(to=Endpoint(interface("", "eth1"))), "blank value"),
        (PathQuery(to=Endpoint(device())), "at least one value"),
        (PathQuery(to=Endpoint(device_vrf("r1", " "))), "a device and a VRF"),
        (PathQuery(from_=Endpoint(device("r1"), headers=[{}])), "at least one field"),
        (PathQuery(from_=Endpoint(device("r1"), headers=[{"tp_dst": [" "]}])), "blank value"),
        (PathQuery(from_=Endpoint(device("r1"), headers=[{"tp_dst": "443"}])), "list of values"),
        (PathQuery(to=Endpoint(device("r1"), headers=[{"app_id": ["web"]}])), "only in from_"),
        (
            PathQuery(chain=[ChainHop("egress", device("r1"), headers=[{"user_id": ["u"]}])]),
            "only in from_",
        ),
        (PathQuery(chain=[ChainHop("ingress", negate(host("h1")))]), "cannot be a hop"),
        (PathQuery(to=Endpoint(security_zone("fw-1", ""))), "a device and a zone"),
        (PathQuery(to=Endpoint(subnet(" "))), "address or prefix"),
        (PathQuery(to=Endpoint(device(["r1"]))), "separate arguments"),  # type: ignore[arg-type]
        (PathQuery(from_=Endpoint(device("r1"), headers={"tp_dst": ["443"]})), "wrap the mapping"),  # type: ignore[arg-type]
    ],
)
def test_bad_queries_are_refused_before_sending(query: PathQuery, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        query.to_api()


def test_from_only_headers_are_allowed_in_from() -> None:
    query = PathQuery(from_=Endpoint(device("r1"), headers=[{"app_id": ["web"]}]))

    assert query.to_api()["from"]["headers"] == [
        {"type": "PacketFilter", "values": {"app_id": ["web"]}}
    ]


def test_hops_carry_their_transit_and_headers() -> None:
    query = PathQuery(
        chain=[
            ChainHop("ingress", interface("fw-1", "eth1"), headers=[{"tp_dst": ["443"]}]),
            ChainHop("egress", vrf("blue")),
        ],
        to=Endpoint(device("r2"), logical_network="lab"),
    )

    assert query.to_api() == {
        "to": {"location": {"type": "DeviceFilter", "values": ["r2"]}, "logicalNetwork": "lab"},
        "chain": [
            {
                "transitType": "ingress",
                "location": {"type": "InterfaceFilter", "values": ["fw-1 eth1"]},
                "headers": [{"type": "PacketFilter", "values": {"tp_dst": ["443"]}}],
            },
            {"transitType": "egress", "location": {"type": "VrfFilter", "values": ["blue"]}},
        ],
    }


@pytest.mark.parametrize(
    "limits", [{"max_results": 0}, {"max_results": 10001}, {"max_duration_seconds": 301}]
)
def test_limits_outside_forwards_range_are_refused(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must be 1"):
        PathQuery(from_=Endpoint(device("r1"))).request(**limits)
