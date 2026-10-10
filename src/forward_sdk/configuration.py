"""Reading Forward's configuration responses.

Every configuration route answers with a one-entry object, ``{property:
value}``, and Forward lower-cases the property name on the wire, so the key
for ``FIREWALL_PREDICT`` is ``firewall_predict``. The value is typed per
property: a boolean, an integer, a string or an enum name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["PropertySource", "config_value", "describe_properties"]


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
