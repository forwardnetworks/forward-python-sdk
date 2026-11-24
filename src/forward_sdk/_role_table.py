"""Forward's table of operations and the roles that hold them, as a value.

This module holds the table, the parsers that read Forward's access source from text
held in memory, and the table's JSON text form. It never opens a file, runs a command
or reads the environment: :mod:`forward_sdk.rbac` does that, and gives the table to
the rules in :mod:`forward_sdk._access`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple, TypeVar

from forward_sdk.errors import ForwardConfigurationError

_T = TypeVar("_T", bound="RoleTable")

__all__ = [
    "NETWORK_ROLES",
    "NoOperationsError",
    "OperationRow",
    "RoleTable",
    "lowest",
    "parse_network_roles",
    "parse_operations",
]

#: Network roles from least to most permitted. Each holds everything the one before it does.
NETWORK_ROLES = ("LIMITED_READ_ONLY", "READ_ONLY", "OPERATOR", "WORKSPACE_OPERATOR", "ADMIN")

_OPERATION = re.compile(
    r"^\s+(?P<name>[A-Z][A-Z0-9_]*)\(AccessType\.(?P<access>READ|WRITE),\s*"
    r"LicenseFacet\.(?P<facet>[A-Z_]+)\)[,;]",
)


class NoOperationsError(ForwardConfigurationError):
    """The access source held no operations: the reader says where it looked."""


class OperationRow(NamedTuple):
    """One operation as Forward declares it."""

    access_type: str
    license_facet: str
    #: The lowest network role that holds it, or ``None`` when only an org admin does.
    lowest_network_role: str | None
    doc: str


def parse_operations(text: str) -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = _OPERATION.match(line)
        if not match:
            continue
        doc: list[str] = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip().startswith("///"):
            doc.insert(0, lines[cursor].strip().removeprefix("///").strip())
            cursor -= 1
        found[match["name"]] = (
            match["access"],
            match["facet"],
            " ".join(part for part in doc if part),
        )
    return found


#: Ways a role's operations could be built that the reader does not follow. Reading past
#: one would quietly give a role the wrong operations, so the reader refuses instead.
_UNFOLLOWED = re.compile(
    r"\.stream\(|\bfilter\(|difference\(|union\(|intersection\(|removeAll|retainAll|\.VALUES\b|allOf\("
)


def parse_network_roles(text: str) -> dict[str, dict[str, set[str]]]:
    """What each network role holds, the roles it builds on folded in."""
    starts = [(role, re.search(rf"^    {role}\(", text, re.MULTILINE)) for role in NETWORK_ROLES]
    missing = [role for role, match in starts if match is None]
    if missing:
        raise ForwardConfigurationError(f"NetworkRole.java has no constant for {missing}")
    end = text.index("public static final ImmutableSet<NetworkRole> VALUES")
    bounds = [match.start() for _, match in starts if match] + [end]
    held: dict[str, dict[str, set[str]]] = {}
    for position, role in enumerate(NETWORK_ROLES):
        block = text[bounds[position] : bounds[position + 1]]
        unfollowed = _UNFOLLOWED.search(block)
        if unfollowed:
            raise ForwardConfigurationError(
                f"NetworkRole.java builds {role} with {unfollowed.group(0)!r}, which this reader "
                "does not follow; reading it anyway could give wrong answers"
            )
        network = set(re.findall(r"NetworkOperation\.([A-Z][A-Z0-9_]*)", block))
        org = set(re.findall(r"OrgOperation\.([A-Z][A-Z0-9_]*)", block))
        for inherited in re.findall(r"([A-Z][A-Z_]+)\.allowedOperations", block):
            network |= held[inherited]["network"]
        for inherited in re.findall(r"([A-Z][A-Z_]+)\.allowedOrgOperations", block):
            org |= held[inherited]["org"]
        held[role] = {"network": network, "org": org}
    return held


def lowest(name: str, kind: str, held: Mapping[str, Mapping[str, set[str]]]) -> str | None:
    for role in NETWORK_ROLES:
        if name in held[role][kind]:
            return role
    return None


@dataclass(frozen=True, slots=True)
class RoleTable:
    """Every operation, and the lowest network role that holds each."""

    network_operations: Mapping[str, OperationRow]
    org_operations: Mapping[str, OperationRow]
    #: Where it was read from, for the record: a commit, a path, or a file name.
    source: str = ""

    @classmethod
    def from_source_texts(
        cls: type[_T], network_text: str, org_text: str, roles_text: str, source: str = ""
    ) -> _T:
        """Build the table from the text of Forward's three access files."""
        network = parse_operations(network_text)
        org = parse_operations(org_text)
        held = parse_network_roles(roles_text)
        if not network or not org:
            raise NoOperationsError("found no operations in the access source")
        for role, sets in held.items():
            unknown = (sets["network"] - set(network)) | (sets["org"] - set(org))
            if unknown:
                raise ForwardConfigurationError(
                    f"{role} names operations that do not exist: {sorted(unknown)}"
                )
        return cls(
            {
                n: OperationRow(a, f, lowest(n, "network", held), d)
                for n, (a, f, d) in network.items()
            },
            {n: OperationRow(a, f, lowest(n, "org", held), d) for n, (a, f, d) in org.items()},
            source,
        )

    @classmethod
    def from_json_text(cls: type[_T], text: str, name: str = "table") -> _T:
        """Read a table written by :meth:`to_json`, from its text."""
        try:
            raw = json.loads(text)
            return cls(
                {n: OperationRow(*row) for n, row in raw["network_operations"].items()},
                {n: OperationRow(*row) for n, row in raw["org_operations"].items()},
                raw.get("source") or f"file {name}",
            )
        except (ValueError, KeyError, TypeError) as error:
            raise ForwardConfigurationError(f"{name} is not a role table: {error}") from error

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "network_operations": {n: list(r) for n, r in self.network_operations.items()},
                "org_operations": {n: list(r) for n, r in self.org_operations.items()},
            },
            indent=1,
        )
