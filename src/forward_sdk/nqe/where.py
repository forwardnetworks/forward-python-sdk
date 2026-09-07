"""Building NQE predicate clauses.

Integrations scope queries at runtime -- to a set of device tags, to a subset of
a network -- which means assembling query text from user-supplied values. Doing
that with string formatting invites both broken queries and injected clauses, so
every value goes through :func:`literal`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Literal

__all__ = ["literal", "membership", "tag_scope", "where"]

MatchMode = Literal["any", "all"]


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
    """Build a clause testing ``expression`` against ``values``.

    NQE's membership operators are ``in`` and ``not in``, and the element goes
    on the left: ``"core" in device.tagNames``.

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
