"""Classic-device connection types, and the result of creating a device with its credential.

``client.classic_devices.list_device_conn_types()`` answers a map of type id to
metadata. Without ``for_="ui"`` it lists only the types Forward offers to end
users; with it, every type that can be a classic device, internal and
in-development ones included. :func:`conn_type_exposed` applies Forward's own
rule to one entry. Mirrors the Go SDK (v0.8.55).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CONN_TYPE_INTERNAL",
    "CONN_TYPE_K8S_CNI_CONTROL_PLANE_API",
    "CONN_TYPE_K8S_CNI_NODE_API",
    "CONN_TYPE_K8S_CNI_POD_API",
    "CONN_TYPE_NOT_SUPPORTED",
    "CONN_TYPE_PRODUCTION",
    "CONN_TYPE_TECH_PREVIEW",
    "CONN_TYPE_UNDER_DEVELOPMENT",
    "ClassicDeviceWithCredential",
    "conn_type_exposed",
]

#: A connection type's ``status`` (Forward's ``DeviceConnType.SupportStatus``).
CONN_TYPE_PRODUCTION = "PRODUCTION"
CONN_TYPE_TECH_PREVIEW = "TECH_PREVIEW"
CONN_TYPE_INTERNAL = "INTERNAL"
CONN_TYPE_UNDER_DEVELOPMENT = "UNDER_DEVELOPMENT"
CONN_TYPE_NOT_SUPPORTED = "NOT_SUPPORTED"

#: Kubernetes CNI connection types. They are ``UNDER_DEVELOPMENT``: Forward
#: accepts and collects them, but only its development parser (the org property
#: ``PARSER=DEV``) turns the data into devices. The control-plane type takes an
#: ``API_KEY`` HTTP credential, the cluster's bearer token (see
#: ``networks.create_classic_device_with_api_key``); the node and pod types take
#: a ``LOGIN`` one. Forward names the devices it generates ``<source>_<node>``
#: and ``<source>_<namespace>_<pod>``.
CONN_TYPE_K8S_CNI_CONTROL_PLANE_API = "k8s_cni_control_plane_api"
CONN_TYPE_K8S_CNI_NODE_API = "k8s_cni_node_api"
CONN_TYPE_K8S_CNI_POD_API = "k8s_cni_pod_api"

_EXPOSED = frozenset({CONN_TYPE_PRODUCTION, CONN_TYPE_TECH_PREVIEW})


def conn_type_exposed(info: Mapping[str, Any]) -> bool:
    """Whether Forward offers this connection type to end users.

    Forward's own rule (``isExposed``): ``PRODUCTION`` and ``TECH_PREVIEW`` only.
    A missing or unknown status is not exposed.
    """
    return info.get("status") in _EXPOSED


@dataclass(frozen=True, slots=True)
class ClassicDeviceWithCredential:
    """What ``networks.create_classic_device_with_api_key`` made.

    Attributes:
        credential: The ``API_KEY`` HTTP credential (``HttpCredential``).
        device: The classic device that uses it (``ClassicDevice``).
    """

    credential: Any
    device: Any
