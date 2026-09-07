"""Building NQE predicate clauses.

Integrations scope queries at runtime -- to a set of device tags, to a subset of
a network -- which means assembling query text from user-supplied values. Doing
that with string formatting invites both broken queries and injected clauses, so
every value goes through :func:`literal`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from forward_sdk.nqe.enums import members as nqe_members
from forward_sdk.nqe.enums import suggest

__all__ = ["enum_one_of", "literal", "membership", "one_of", "tag_scope", "where"]

MatchMode = Literal["any", "all"]

#: An NQE identifier: an enum member or type name. Used to validate rather than
#: escape, since neither can be quoted without changing its meaning to a string.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def literal(value: object) -> str:
    """Render a Python value as an NQE literal.

    JSON encoding is used deliberately: NQE's string, number and boolean
    literals match JSON's, so this escapes quotes, backslashes and control
    characters correctly rather than approximately.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))


def membership(
    expression: str,
    values: Sequence[str],
    *,
    match: MatchMode = "any",
    negate: bool = False,
) -> str | None:
    """Test whether a **collection** contains any of ``values``.

    Emits ``"value" in expression``, so ``expression`` must be a collection --
    ``device.tagNames``, ``device.groupNames``. For a scalar field such as
    ``device.platform.vendor`` use :func:`one_of` instead: asking whether a
    string is a member of a scalar matches nothing, and a probe built that way
    reports an empty scope on a perfectly healthy network, which reads like a
    data problem rather than a predicate one.

    Args:
        expression: The collection to test against, e.g. ``device.tagNames``.
        values: Values to test for. Empty means no constraint.
        match: Whether one value must be present (``any``) or all of them
            (``all``). Ignored when ``negate`` is set.
        negate: Exclude anything carrying *any* of the values. Exclusion is
            all-or-nothing by nature: "exclude these tags" means none of them
            may be present, so ``match`` does not apply.

    Returns:
        The clause, or ``None`` when ``values`` is empty, so callers can drop
        an unconstrained filter rather than emit ``where true``.
    """
    items = [value for value in values if value]
    if not items:
        return None

    operator = "not in" if negate else "in"
    tests = [f"{literal(value)} {operator} {expression}" for value in items]
    if len(tests) == 1:
        return tests[0]

    # Excluding several values means none of them may be present, so the tests
    # are joined with && whatever `match` says.
    joiner = " && " if (negate or match == "all") else " || "
    return f"({joiner.join(tests)})"


def one_of(field: str, values: Sequence[Any]) -> str | None:
    """Test whether a **string-valued** field equals any of ``values``.

    Emits ``field in ["a", "b"]``, the other direction from :func:`membership`:
    here the field is on the left and the collection is the literal.

    The field must hold a string. NQE compares by type, so applying this to an
    enum field such as ``device.platform.vendor`` fails at run time with *the
    type of lookup value Vendor is not equal to list element type String*. Use
    :func:`enum_one_of` for those; ``model`` and ``osVersion`` are strings and
    belong here.

    Returns:
        The clause, or ``None`` when ``values`` is empty.
    """
    items = [value for value in values if value is not None and value != ""]
    if not items:
        return None
    rendered = ", ".join(literal(value) for value in items)
    return f"{field} in [{rendered}]"


def enum_one_of(field: str, enum_type: str, values: Sequence[Any]) -> str | None:
    """Test whether an **enum** field equals any of ``values``.

    Emits ``(f == Vendor.ARISTA || f == Vendor.CISCO)``. NQE enum values are
    named constants rather than strings, so they are compared directly and are
    deliberately *not* quoted:

        >>> enum_one_of("device.platform.vendor", "Vendor", ["ARISTA", "CISCO"])
        '(device.platform.vendor == Vendor.ARISTA || device.platform.vendor == Vendor.CISCO)'

    ``enum_type`` is the name of the type **in NQE**, which is not always the
    name of the corresponding SDK model class. NQE's data model is its own
    namespace, and the two disagree: ``device.platform.os`` has NQE type ``OS``
    while the SDK model class is ``VendorOs``, and passing ``VendorOs`` fails
    with *Variable VendorOs not in scope*.

    Forward documents the NQE data model, and that is the authoritative source
    for these names:

        https://docs.fwd.app/latest/application/nqe/language/data-model/

    A ``toString()`` rendering confirms a name rather than discovering it, since
    it prefixes the member with the type name::

        toString(device.platform.os)      -> "OS.PAN_OS"          so "OS"
        toString(device.platform.vendor)  -> "Vendor.CISCO"       so "Vendor"

    Member names are checked against NQE's own data model, so a name NQE would
    reject fails here instead of at query time. Do not take them from
    :mod:`forward_sdk.models`: those describe Forward's REST schema, and the two
    namespaces disagree. Measured against a live instance, ``Vendor`` alone
    diverges three ways::

        Vendor.MICROSOFT  rejected by NQE; the REST model has it
        Vendor.AZURE      accepted by NQE; the REST model does not have it
        Vendor.GD         rejected; NQE spells it GENERAL_DYNAMICS

    :mod:`forward_sdk.nqe.enums` carries the NQE names and is what this checks
    against.

    Args:
        field: The enum-valued field.
        enum_type: The NQE type name. See above: not necessarily the SDK class
            name.
        values: Members, either as :class:`~enum.Enum` values or as names. A
            name is validated as an identifier rather than quoted, since a
            quoted value would be a string and fail the comparison.

    Returns:
        The clause, or ``None`` when ``values`` is empty.

    Raises:
        ValueError: If a member name could not be an NQE identifier, or if NQE
            has no such member for a type it knows. Enum members cannot be
            escaped the way a string can, so anything unexpected is refused
            rather than interpolated.
    """
    type_name = str(enum_type)
    members = [str(value).strip() for value in values if str(value).strip()]
    if not members:
        return None
    for member in members:
        if not IDENTIFIER.fullmatch(member):
            raise ValueError(
                f"{member!r} is not a valid enum member name. Enum values are "
                "compared as identifiers and cannot be quoted or escaped."
            )
    if not IDENTIFIER.fullmatch(type_name):
        raise ValueError(f"{type_name!r} is not a valid NQE type name")
    _check_members(type_name, members)

    tests = [f"{field} == {type_name}.{member}" for member in members]
    return tests[0] if len(tests) == 1 else f"({' || '.join(tests)})"


def _check_members(type_name: str, members: Sequence[str]) -> None:
    """Reject a member NQE does not have, while the caller can still see why.

    Only checked for types the data model knows. An unknown type is left alone,
    since the model covers the network schema rather than every namespace a
    query might reach.
    """
    known = nqe_members(type_name)
    if not known:
        return
    for member in members:
        if member in known:
            continue
        close = suggest(type_name, member)
        hint = f" Did you mean {' or '.join(close)}?" if close else ""
        raise ValueError(
            f"NQE has no member {member!r} for type {type_name!r}.{hint} "
            "Note that forward_sdk.models describes the REST schema, which "
            "differs from NQE; forward_sdk.nqe.enums has the NQE names."
        )


def where(*clauses: str | None) -> str:
    """Join clauses into ``where`` lines, skipping empty ones."""
    return "\n".join(f"where {clause}" for clause in clauses if clause)


def tag_scope(
    expression: str = "device.tagNames",
    *,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    include_match: MatchMode = "any",
) -> str:
    """Build the ``where`` lines that scope a query to a set of tags.

    Include and exclude are independent: with neither, nothing is emitted and
    the query is unscoped.
    """
    return where(
        membership(expression, list(include), match=include_match),
        membership(expression, list(exclude), negate=True),
    )
