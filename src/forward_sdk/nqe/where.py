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

    Args:
        expression: The NQE expression to test, e.g. ``device.tagNames``.
        values: Values to test against. Empty means no constraint.
        match: ``any`` requires one match, ``all`` requires every value.
        negate: Require that none of the values match.

    Returns:
        The clause, or ``None`` when ``values`` is empty, so callers can drop
        an unconstrained filter rather than emit ``where true``.
    """
    items = [v for v in values if v]
    if not items:
        return None

    tests = [f"{expression} contains {literal(value)}" for value in items]
    joiner = " && " if (match == "all") != negate else " || "
    clause = joiner.join(tests)
    if negate:
        return f"!({clause})" if len(tests) > 1 else f"!{clause}"
    return f"({clause})" if len(tests) > 1 else clause


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
