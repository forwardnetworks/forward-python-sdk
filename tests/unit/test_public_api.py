"""The published names SDK users import, pinned to what Forward expects.

Each name below is in a module's ``__all__`` and documented, and nothing in the
SDK itself uses it: it exists for callers. A rename or removal here breaks
``from forward_sdk.X import NAME`` for every user, so each one is imported by
name and its value checked against Forward's own definition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from forward_sdk._generated.models import (
    BgpAdvertisement,
    BgpAdvertisementKey,
    CheckType,
    TransitType,
)
from forward_sdk.bgp import key_of
from forward_sdk.change_report import SCHEMA_VERSION
from forward_sdk.checks import EXISTENTIAL, ISOLATION, NQE, PREDEFINED, REACHABILITY
from forward_sdk.classic_devices import (
    CONN_TYPE_INTERNAL,
    CONN_TYPE_K8S_CNI_CONTROL_PLANE_API,
    CONN_TYPE_K8S_CNI_NODE_API,
    CONN_TYPE_K8S_CNI_POD_API,
    CONN_TYPE_NOT_SUPPORTED,
    CONN_TYPE_PRODUCTION,
    CONN_TYPE_TECH_PREVIEW,
    CONN_TYPE_UNDER_DEVELOPMENT,
)
from forward_sdk.configuration import (
    ADVANCED_REACHABILITY_ANALYSIS,
    ADVANCED_REACHABILITY_ASYNC,
    ADVANCED_REACHABILITY_ON_DEMAND,
    ALERT_NEW_DEVICE_FAILURE_PERCENTAGE,
    DISABLE_FLOW_COMPUTATION,
)
from forward_sdk.nqe.state import BGP_SESSION_ESTABLISHED, INTERFACE_ADMIN_UP
from forward_sdk.trace import Transit, interface_values

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_check_types_are_forwards_check_type_names() -> None:
    # The API's CheckType enum is the source of truth for the names.
    api = {member.value for member in CheckType}
    assert {EXISTENTIAL, ISOLATION, REACHABILITY, NQE, PREDEFINED} <= api
    assert (EXISTENTIAL, ISOLATION, REACHABILITY, NQE, PREDEFINED) == (
        "Existential",
        "Isolation",
        "Reachability",
        "NQE",
        "Predefined",
    )


def test_connection_type_statuses_and_k8s_ids_are_forwards() -> None:
    # Forward's DeviceConnType.SupportStatus and its Kubernetes CNI type ids.
    assert (
        CONN_TYPE_PRODUCTION,
        CONN_TYPE_TECH_PREVIEW,
        CONN_TYPE_INTERNAL,
        CONN_TYPE_UNDER_DEVELOPMENT,
        CONN_TYPE_NOT_SUPPORTED,
    ) == ("PRODUCTION", "TECH_PREVIEW", "INTERNAL", "UNDER_DEVELOPMENT", "NOT_SUPPORTED")
    assert (
        CONN_TYPE_K8S_CNI_CONTROL_PLANE_API,
        CONN_TYPE_K8S_CNI_NODE_API,
        CONN_TYPE_K8S_CNI_POD_API,
    ) == ("k8s_cni_control_plane_api", "k8s_cni_node_api", "k8s_cni_pod_api")


def test_network_property_names_are_the_lowercase_wire_spelling() -> None:
    # Forward's NetworkProperty names, as a 26.10.6 appserver answers and accepts them.
    assert (
        DISABLE_FLOW_COMPUTATION,
        ALERT_NEW_DEVICE_FAILURE_PERCENTAGE,
        ADVANCED_REACHABILITY_ANALYSIS,
    ) == (
        "disable_flow_computation",
        "alert_new_device_failure_percentage",
        "advanced_reachability_analysis",
    )
    assert (ADVANCED_REACHABILITY_ASYNC, ADVANCED_REACHABILITY_ON_DEMAND) == ("ASYNC", "ON_DEMAND")


def test_state_values_are_what_the_nqe_reads_return() -> None:
    # Seen on live reads; tests/unit/test_nqe_state.py uses them as row data.
    assert INTERFACE_ADMIN_UP == "AdminStatus.UP"
    assert BGP_SESSION_ESTABLISHED == "ESTABLISHED"


def test_trace_transit_names_forwards_three_hop_kinds() -> None:
    # The spec's TransitType is the source of truth; order does not matter.
    assert set(get_args(Transit)) == {member.value for member in TransitType}


def test_interface_values_takes_forwards_device_interface_strings() -> None:
    # What callers depend on is the filter Forward receives.
    location = interface_values("r1 et1", "r2 et2")
    location.check()
    assert location.to_api() == {"type": "InterfaceFilter", "values": ["r1 et1", "r2 et2"]}


def test_key_of_is_the_vrf_peer_prefix_key() -> None:
    advertisement = BgpAdvertisement.model_validate(
        {"vrf": "default", "externalPeer": "isp-a", "prefix": "192.0.2.0/24", "med": 10}
    )
    assert key_of(advertisement) == BgpAdvertisementKey(
        vrf="default", external_peer="isp-a", prefix="192.0.2.0/24"
    )
    key = BgpAdvertisementKey(vrf="v", external_peer="p", prefix="198.51.100.0/24")
    assert key_of(key) == key


def test_schema_version_is_the_shared_specs() -> None:
    # The change-report schema shared byte for byte with the Go SDK.
    schema = json.loads((FIXTURES / "change_report" / "schema.json").read_text(encoding="utf-8"))
    report = json.loads(
        (FIXTURES / "change_report" / "regression" / "report.json").read_text(encoding="utf-8")
    )
    assert SCHEMA_VERSION == report["meta"]["schema_version"] == "1"
    meta = schema["properties"]["meta"]["properties"]
    assert meta["schema_version"] == {"const": SCHEMA_VERSION}
