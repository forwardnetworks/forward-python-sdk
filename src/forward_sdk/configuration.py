"""Reading Forward's configuration responses.

Every configuration route answers with a one-entry object, ``{property:
value}``, and Forward lower-cases the property name on the wire, so the key
for ``FIREWALL_PREDICT`` is ``firewall_predict``. The value is typed per
property: a boolean, an integer, a string or an enum name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "ADVANCED_REACHABILITY_ANALYSIS",
    "ADVANCED_REACHABILITY_ASYNC",
    "ADVANCED_REACHABILITY_ON_DEMAND",
    "ALERT_NEW_DEVICE_FAILURE_PERCENTAGE",
    "DISABLE_FLOW_COMPUTATION",
    "PropertySource",
    "config_value",
    "describe_properties",
]

#: The network properties Forward defines (``NetworkProperty``, a subset of the
#: org properties), in the lowercase spelling a live 26.10.6 appserver answers
#: and accepts. Set one per network with ``configuration.set_network_config_property``
#: and clear it with ``delete_network_config_property``. A build may define more
#: or fewer; an unknown name, such as the org-only ``location_connectivity_diffs``,
#: is a 400 (``error.unknown_network_property``). Mirrors the Go SDK v0.8.40.
DISABLE_FLOW_COMPUTATION = "disable_flow_computation"
ALERT_NEW_DEVICE_FAILURE_PERCENTAGE = "alert_new_device_failure_percentage"
ADVANCED_REACHABILITY_ANALYSIS = "advanced_reachability_analysis"

#: Values of ``advanced_reachability_analysis``: ``ON_DEMAND`` (the default)
#: computes the advanced analysis only when a user asks; ``ASYNC`` computes it
#: after each snapshot finishes processing. The AutoCon6 workshop set ``ASYNC``
#: on 67 networks with an org admin's key on 26.10.6.
ADVANCED_REACHABILITY_ASYNC = "ASYNC"
ADVANCED_REACHABILITY_ON_DEMAND = "ON_DEMAND"


def config_value(response: Any) -> Any:
    """The value out of a configuration response, whatever the key.

    Accepts the generated ``ConfigValue`` model or a plain mapping. Returns
    ``None`` for an empty response, which Forward does not send for a known
    property; an unknown property is a 400 instead.

    The three routes answer differently for the same property and the
    difference is the point. The global route reports the fixed default and
    never an org's override, so it keeps saying ``False`` after an org has set
    ``True``; the org-effective routes report what actually governs. Read from
    the scope you care about, not the one that happens to be reachable.
    """
    data = (
        response.model_dump(by_alias=True)
        if hasattr(response, "model_dump")
        else dict(response or {})
    )
    for value in data.values():
        return value
    return None


@dataclass(frozen=True, slots=True)
class PropertySource:
    """A property's effective value and where it comes from.

    Attributes:
        value: What takes effect for the org.
        source: ``org`` when the org sets it, ``global`` when a Forward
            administrator changed the global default, ``default`` when it is the
            value compiled into Forward. An org setting counts as ``org`` even
            if it happens to equal the default.
    """

    value: Any
    source: str


def _entries(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return dict(response.model_dump(by_alias=True))
    return dict(response or {})


def describe_properties(
    effective: Any, org_configured: Any, global_configured: Any
) -> dict[str, PropertySource]:
    """Say, for every property, what it is set to and which scope set it.

    Forward has no route that reports a value's source, so it is derived from
    three listings, each of which the client can fetch::

        effective = client.configuration.get_org_config_values(filter="OFF")
        org = client.configuration.get_org_config_values(filter="CONFIGURED")
        glob = client.configuration.get_global_config_values(filter="CONFIGURED")
        sources = describe_properties(effective, org, glob)

    For another org, as a Forward support account, read that org's listings
    instead: ``get_org_scoped_config_values(org_id=..., filter="OFF")`` and
    ``filter="CONFIGURED"``. The per-property overrides list
    (``get_global_config_property_org_overrides``) is not a substitute: it holds
    stored rows, including those of deleted orgs, not effective values.

    The org wins if it set the property, then the global scope, and otherwise the
    value is the compiled default. No route exposes that default on its own, so
    ``default`` means "nobody changed it", not a value you can read back.

    Arguments may be the generated ``ConfigValues`` models or plain mappings.
    Property names are compared case-insensitively, since Forward lower-cases
    them on the wire and documents them in upper case.
    """
    org = {name.lower() for name in _entries(org_configured)}
    glob = {name.lower() for name in _entries(global_configured)}
    described: dict[str, PropertySource] = {}
    for name, value in _entries(effective).items():
        key = name.lower()
        source = "org" if key in org else "global" if key in glob else "default"
        described[name] = PropertySource(value=value, source=source)
    return described
