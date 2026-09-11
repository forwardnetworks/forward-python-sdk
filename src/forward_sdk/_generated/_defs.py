"""Types describing a Forward API operation.

Hand-written; the data that uses these types is generated into
``_generated/operations.py`` by ``scripts/gen_operations.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Gating a caller may hit on an operation. These are documentation hints only.
#: Forward reports a missing licence, an undeployed feature and an RBAC denial
#: with the same ordinary status codes, so the SDK cannot tell them apart. See
#: ``docs/gating.md``.
Gating = str

#: Whether an operation appears in Forward's published OpenAPI description.
#: ``"unpublished"`` operations are stable and safe to use -- Forward's own
#: integrations depend on them -- but their shapes are described by hand here,
#: so a change to one would not show up in a spec diff.
Stability = str


@dataclass(frozen=True, slots=True)
class ParamDef:
    """A single path, query or header parameter."""

    name: str
    location: str
    required: bool = False
    explode: bool = True
    schema_type: str | None = None
    enum: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True, slots=True)
class OpDef:
    """One operation, keyed in :data:`OPERATIONS` by its ``operationId``.

    ``path`` never contains a query string. Forward dispatches some operations
    by a fixed query string on a shared path (``?action=addBatch``); that is
    carried separately in :attr:`fixed_query` so request building can merge it
    with caller-supplied parameters.
    """

    operation_id: str
    method: str
    path: str
    tag: str
    fixed_query: tuple[tuple[str, str | None], ...] = ()
    parameters: tuple[ParamDef, ...] = ()
    parent_tag: str | None = None
    deprecated: bool = False
    request_media: tuple[str, ...] = ()
    response_media: tuple[str, ...] = ()
    stability: Stability = "published"
    gating: tuple[Gating, ...] = field(default_factory=tuple)
    response_model: str | None = None
    response_is_array: bool = False
    summary: str | None = None
    stream: bool = False

    @property
    def path_params(self) -> tuple[ParamDef, ...]:
        return tuple(p for p in self.parameters if p.location == "path")

    @property
    def query_params(self) -> tuple[ParamDef, ...]:
        return tuple(p for p in self.parameters if p.location == "query")

    def __str__(self) -> str:
        query = "".join(f"?{k}" if v is None else f"?{k}={v}" for k, v in self.fixed_query[:1])
        return f"{self.method.upper()} {self.path}{query}"


#: Tags whose service classes are written by hand rather than generated.
#:
#: One list, read by the service generator to know what to skip and by the
#: coverage test to know what to check by operation id, so the two cannot
#: disagree about which is which. Each entry says why it is hand-written.
HAND_WRITTEN_TAGS: frozenset[str] = frozenset(
    {
        "Network Management",
        "Network Snapshots",
        "Network Devices",
        "Device Tags",
        "NQE",
        "Current Version",
        "NQE Repository",
        "Snapshot Reachability",
        # Answers arrive asynchronously, so this needs a chat handle with a
        # poll loop rather than a method per endpoint.
        "Forward AI",
        # The connectivity comparison is computed asynchronously and an early
        # read returns zeros that look like "nothing changed", so this needs a
        # wait loop and a docstring about the ambiguous settled zero.
        "Snapshot Diffs",
        # Two routes take text/plain bodies, predict needs a wait for the
        # snapshot it creates, and the workflow reads better as a handle.
        "Change Sets",
    }
)
