"""Building check definitions: path checks from a trace query, and NQE checks.

A path check's definition is Forward's intent check: the check type, the path
query as ``filters`` (the same shape the Search page sends, so
:class:`forward_sdk.trace.PathQuery` builds it) and optional noise types and
header fields. Pass the definition as ``definition`` in the body of
``client.checks.add_check``::

    from forward_sdk import checks
    from forward_sdk.trace import ChainHop, Endpoint, PathQuery, device, subnet

    flow = PathQuery(
        from_=Endpoint(subnet("10.10.10.10/32")),
        chain=[ChainHop("through", device("r2"))],
        to=Endpoint(subnet("10.20.20.20/32"), headers=[{"ip_proto": ["6"], "tp_dst": ["8080"]}]),
    )
    client.checks.add_check(
        snapshot_id=snapshot_id,
        body={"definition": checks.existential(flow), "name": "app via r2"},
        persistent=True,
    )

Source: Forward's ``IntentCheck``, ``ExistsCheck``, ``NqeCheck`` and
``ApiCheckType``. Existential and Isolation checks of this shape, and NQE
checks, were used live on 26.10.6 by the AutoCon6 workshop, persistent ones
judged on predicted snapshots too. Mirrors the Go SDK v0.8.41, and its
validation rules as corrected in v0.8.47 and v0.8.48.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.trace import PathQuery

__all__ = [
    "EXISTENTIAL",
    "ISOLATION",
    "NQE",
    "PREDEFINED",
    "REACHABILITY",
    "existential",
    "isolation",
    "nqe_check",
    "reachability",
    "validate_new_check",
]

#: Check types, as Forward names them.
EXISTENTIAL = "Existential"
ISOLATION = "Isolation"
REACHABILITY = "Reachability"
NQE = "NQE"
PREDEFINED = "Predefined"

#: The header fields Forward lets a check assume a default for.
_DEFAULTABLE_FIELDS = frozenset(
    {"ip_tos", "ip_frag_offset", "tp_src", "tcp_flags", "app_id", "url", "user_id"}
)  # SearchUtils.ALLOWED_HEADER_FIELDS_WITH_DEFAULTS


def _intent(
    check_type: str,
    query: PathQuery,
    *,
    noise_types: Sequence[str],
    header_fields_with_defaults: Sequence[str],
    return_path: str | None,
) -> dict[str, Any]:
    definition: dict[str, Any] = {"checkType": check_type, "filters": query.to_api()}
    if noise_types:
        definition["noiseTypes"] = list(noise_types)
    if header_fields_with_defaults:
        unknown = sorted(set(header_fields_with_defaults) - _DEFAULTABLE_FIELDS)
        if unknown:
            raise ForwardConfigurationError(
                f"header_fields_with_defaults takes only {', '.join(sorted(_DEFAULTABLE_FIELDS))}; "
                f"Forward refuses {', '.join(unknown)}"
            )
        definition["headerFieldsWithDefaults"] = list(header_fields_with_defaults)
    if return_path is not None:  # only existential() passes one
        if return_path not in ("ANY", "SYMMETRIC"):
            raise ForwardConfigurationError(
                f"return_path must be ANY or SYMMETRIC, not {return_path!r}"
            )
        definition["returnPath"] = return_path
    return definition


def existential(
    query: PathQuery,
    *,
    noise_types: Sequence[str] = (),
    header_fields_with_defaults: Sequence[str] = (),
    return_path: Literal["ANY", "SYMMETRIC"] | None = None,
) -> dict[str, Any]:
    """A check that passes when the traffic ``query`` describes has a path.

    Give the query's ``from_`` and ``to`` and header filters, and a chain hop to
    require one ("through r2"). ``return_path`` also requires a path back:
    ``"ANY"``, or ``"SYMMETRIC"`` for one through the same devices in reverse.
    ``noise_types`` are traffic classes to ignore (Forward's ``NoiseType``
    names), and ``header_fields_with_defaults`` the fields Forward may assume a
    default for (only ``ip_tos``, ``ip_frag_offset``, ``tp_src``,
    ``tcp_flags``, ``app_id``, ``url`` and ``user_id``).
    """
    return _intent(
        EXISTENTIAL,
        query,
        noise_types=noise_types,
        header_fields_with_defaults=header_fields_with_defaults,
        return_path=return_path,
    )


def isolation(
    query: PathQuery,
    *,
    noise_types: Sequence[str] = (),
    header_fields_with_defaults: Sequence[str] = (),
) -> dict[str, Any]:
    """A check that passes when the traffic ``query`` describes has NO path."""
    return _intent(
        ISOLATION,
        query,
        noise_types=noise_types,
        header_fields_with_defaults=header_fields_with_defaults,
        return_path=None,
    )


def reachability(
    query: PathQuery,
    *,
    noise_types: Sequence[str] = (),
    header_fields_with_defaults: Sequence[str] = (),
) -> dict[str, Any]:
    """A check that passes when ALL the traffic ``query`` describes is delivered."""
    return _intent(
        REACHABILITY,
        query,
        noise_types=noise_types,
        header_fields_with_defaults=header_fields_with_defaults,
        return_path=None,
    )


def nqe_check(query_id: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A check that runs a published NQE library query (``Q_...``), with optional parameters.

    Without ``params`` the check takes its name from the query, and Forward
    answers 400 to a ``name`` beside it (seen live on 26.10.6), so send
    ``{"definition": nqe_check(...)}`` alone. With ``params`` a name is
    REQUIRED. An NQE check never takes a note or tags. See
    :func:`validate_new_check`.
    """
    if not query_id or not query_id.strip():
        raise ForwardConfigurationError("an NQE check needs a library query id")
    definition: dict[str, Any] = {"checkType": NQE, "queryId": query_id.strip()}
    if params:
        definition["params"] = dict(params)
    return definition


#: The path check types: the only ones that take tags.
_PATH_TYPES = frozenset({EXISTENTIAL, ISOLATION, REACHABILITY})

#: Forward's older check type names, which it still accepts (``CheckType.fromJson``).
_LEGACY_TYPES = {
    "Exists": EXISTENTIAL,
    "ExistentialCheck": EXISTENTIAL,
    "IsolationCheck": ISOLATION,
    "ReachabilityCheck": REACHABILITY,
    "NetworkQuery": NQE,
}


#: What Java's ``String.trim()`` strips: every character up to U+0020, and no other.
_JAVA_TRIM = "".join(map(chr, range(0x21)))


def _text(value: Any) -> str:
    """A name or note as Forward reads it: trimmed as Java trims, so blank counts as absent."""
    return value.strip(_JAVA_TRIM) if isinstance(value, str) else ""


def validate_new_check(body: Any) -> None:
    """Refuse, without a request, a check body Forward would answer with a 400.

    Forward's rules (``NewNetworkCheck``): no ``name`` on a Predefined or a
    non-parameterized NQE check, a ``name`` required on a parameterized NQE
    check, no ``note`` on an NQE check, ``tags`` only on a path check
    (Existential, Isolation, Reachability), and ``perfMonitoringEnabled`` only
    on an Existential one. ``add_check`` does not call this (a build may accept
    what another refuses); call it where an early, plain message helps. Takes
    the body as a mapping with Forward's field names, or a request model.
    Names and notes are trimmed, as Forward does, and Forward's older type
    names (``ExistentialCheck``, ``NetworkQuery`` and so on) count as the
    current ones.

    Raises:
        ForwardConfigurationError: The body has no definition, or breaks a rule.
    """
    body_api = getattr(body, "to_api", None)
    if callable(body_api):
        body = body_api()
    if not isinstance(body, Mapping):
        raise ForwardConfigurationError(
            f"a check body is a mapping or a request model, not {type(body).__name__}"
        )
    definition: Any = body.get("definition")
    to_api = getattr(definition, "to_api", None)
    if callable(to_api):
        definition = to_api()
    if not isinstance(definition, Mapping):
        raise ForwardConfigurationError("a check body needs a definition")
    raw_kind = str(definition.get("checkType") or "")
    kind = _LEGACY_TYPES.get(raw_kind, raw_kind)
    name = _text(body.get("name"))
    if kind == PREDEFINED and name:
        raise ForwardConfigurationError("a Predefined check takes no name (Forward answers 400)")
    if kind == NQE:
        if definition.get("params"):
            if not name:
                raise ForwardConfigurationError(
                    "a parameterized NQE check needs a name (Forward answers 400)"
                )
        elif name:
            raise ForwardConfigurationError(
                "an NQE check without parameters takes no name: it comes from the query "
                "(Forward answers 400)"
            )
        if _text(body.get("note")):
            raise ForwardConfigurationError("an NQE check takes no note (Forward answers 400)")
    if body.get("tags") and kind not in _PATH_TYPES:
        raise ForwardConfigurationError(
            f"a {kind or 'check'} check takes no tags; only path checks do (Forward answers 400)"
        )
    if body.get("perfMonitoringEnabled") is not None and kind != EXISTENTIAL:
        raise ForwardConfigurationError(
            "perfMonitoringEnabled applies only to an Existential check (Forward answers 400)"
        )
