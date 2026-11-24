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

import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from forward_sdk import _role_table
from forward_sdk._role_table import NETWORK_ROLES, OperationRow
from forward_sdk.errors import ForwardConfigurationError

__all__ = ["NETWORK_ROLES", "OperationRow", "RoleTable", "discover"]

ACCESS_DIR = Path("app/src/main/java/com/forwardnetworks/cv/access")


@dataclass(frozen=True, slots=True)
class RoleTable(_role_table.RoleTable):
    """Every operation, and the lowest network role that holds each.

    The table value and its JSON text form are in :mod:`forward_sdk._role_table`; this adds
    the readers of a Forward source checkout and of a JSON file.
    """

    @classmethod
    def from_checkout(cls, path: str | Path) -> RoleTable:
        """Read the table from a checkout of Forward's source."""
        root = Path(path).expanduser()
        access = root / ACCESS_DIR
        try:
            network_text = (access / "NetworkOperation.java").read_text("utf-8")
            org_text = (access / "OrgOperation.java").read_text("utf-8")
            roles_text = (access / "NetworkRole.java").read_text("utf-8")
        except OSError as error:
            raise ForwardConfigurationError(
                f"{root} does not look like a Forward source checkout: {error}"
            ) from error
        try:
            table = cls.from_source_texts(network_text, org_text, roles_text)
        except _role_table.NoOperationsError:
            raise ForwardConfigurationError(f"found no operations under {access}") from None
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return replace(table, source=f"checkout {commit}" if commit else f"checkout {root}")

    @classmethod
    def from_json(cls, path: str | Path) -> RoleTable:
        """Read a table written by :meth:`to_json`."""
        file = Path(path).expanduser()
        try:
            text = file.read_text("utf-8")
        except (OSError, ValueError) as error:
            raise ForwardConfigurationError(f"{file} is not a role table: {error}") from error
        try:
            return cls.from_json_text(text, file.name)
        except ForwardConfigurationError as error:
            cause = error.__cause__ or error
            raise ForwardConfigurationError(f"{file} is not a role table: {cause}") from cause


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
