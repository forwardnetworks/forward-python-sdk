"""Forward's table of operations and the roles that hold them, read from its source.

Forward serves no endpoint for this and its OpenAPI description says nothing about
roles. The table lives in Forward's access package (``NetworkOperation``,
``OrgOperation`` and ``NetworkRole``), so this module reads it from a checkout of
that source, or from a JSON copy someone made from one. It is **not shipped with
the SDK**: the table is Forward's own data, so you point the SDK at your copy.

Two ways to point at it, tried in this order:

* ``FORWARD_RBAC_TABLE``: a JSON file written by ``python -m forward_sdk.rbac_dump
  --fwd <checkout>``. Handy where the checkout is not.
* ``FORWARD_SOURCE``: a checkout of Forward's source, read directly.

Or pass a :class:`RoleTable` where one is accepted. Without a table, role checks
answer "unknown" and say why.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from forward_sdk.errors import ForwardConfigurationError

__all__ = ["NETWORK_ROLES", "OperationRow", "RoleTable", "discover"]

#: Network roles from least to most permitted. Each holds everything the one before it does.
NETWORK_ROLES = ("LIMITED_READ_ONLY", "READ_ONLY", "OPERATOR", "WORKSPACE_OPERATOR", "ADMIN")

ACCESS_DIR = Path("app/src/main/java/com/forwardnetworks/cv/access")

_OPERATION = re.compile(
    r"^\s+(?P<name>[A-Z][A-Z0-9_]*)\(AccessType\.(?P<access>READ|WRITE),\s*"
    r"LicenseFacet\.(?P<facet>[A-Z_]+)\)[,;]",
)


class OperationRow(NamedTuple):
    """One operation as Forward declares it."""

    access_type: str
    license_facet: str
    #: The lowest network role that holds it, or ``None`` when only an org admin does.
    lowest_network_role: str | None
    doc: str


def _parse_operations(text: str) -> dict[str, tuple[str, str, str]]:
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


def _parse_network_roles(text: str) -> dict[str, dict[str, set[str]]]:
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


def _lowest(name: str, kind: str, held: Mapping[str, Mapping[str, set[str]]]) -> str | None:
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
    def from_checkout(cls, path: str | Path) -> RoleTable:
        """Read the table from a checkout of Forward's source."""
        root = Path(path).expanduser()
        access = root / ACCESS_DIR
        try:
            network = _parse_operations((access / "NetworkOperation.java").read_text("utf-8"))
            org = _parse_operations((access / "OrgOperation.java").read_text("utf-8"))
            held = _parse_network_roles((access / "NetworkRole.java").read_text("utf-8"))
        except OSError as error:
            raise ForwardConfigurationError(
                f"{root} does not look like a Forward source checkout: {error}"
            ) from error
        if not network or not org:
            raise ForwardConfigurationError(f"found no operations under {access}")
        for role, sets in held.items():
            unknown = (sets["network"] - set(network)) | (sets["org"] - set(org))
            if unknown:
                raise ForwardConfigurationError(
                    f"{role} names operations that do not exist: {sorted(unknown)}"
                )
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return cls(
            {
                n: OperationRow(a, f, _lowest(n, "network", held), d)
                for n, (a, f, d) in network.items()
            },
            {n: OperationRow(a, f, _lowest(n, "org", held), d) for n, (a, f, d) in org.items()},
            f"checkout {commit}" if commit else f"checkout {root}",
        )

    @classmethod
    def from_json(cls, path: str | Path) -> RoleTable:
        """Read a table written by :meth:`to_json`."""
        file = Path(path).expanduser()
        try:
            raw = json.loads(file.read_text("utf-8"))
            return cls(
                {n: OperationRow(*row) for n, row in raw["network_operations"].items()},
                {n: OperationRow(*row) for n, row in raw["org_operations"].items()},
                raw.get("source") or f"file {file.name}",
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ForwardConfigurationError(f"{file} is not a role table: {error}") from error

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "network_operations": {n: list(r) for n, r in self.network_operations.items()},
                "org_operations": {n: list(r) for n, r in self.org_operations.items()},
            },
            indent=1,
        )


_discovered: dict[tuple[str | None, str | None], RoleTable | None] = {}


def discover() -> RoleTable | None:
    """The table named by the environment, or ``None`` when none is.

    ``FORWARD_RBAC_TABLE`` (a JSON file) wins over ``FORWARD_SOURCE`` (a checkout).
    A path that is set but wrong raises, so a typo is not mistaken for no table.
    """
    key = (os.environ.get("FORWARD_RBAC_TABLE"), os.environ.get("FORWARD_SOURCE"))
    if key not in _discovered:
        table: RoleTable | None = None
        if key[0]:
            table = RoleTable.from_json(key[0])
        elif key[1]:
            table = RoleTable.from_checkout(key[1])
        _discovered[key] = table
    return _discovered[key]
