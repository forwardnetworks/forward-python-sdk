"""Reading Forward's configuration responses.

Every configuration route answers with a one-entry object, ``{property:
value}``, and Forward lower-cases the property name on the wire, so the key
for ``FIREWALL_PREDICT`` is ``firewall_predict``. The value is typed per
property: a boolean, an integer, a string or an enum name.
"""

from __future__ import annotations

from typing import Any

__all__ = ["config_value"]


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
