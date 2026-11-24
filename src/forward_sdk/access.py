"""What an account may do, worked out from the roles Forward reports for it.

Forward has no call that answers "may I do X on network Y?", and its API
description says nothing about roles. What it does have is a table, in its own
source: every operation, and the lowest role that holds it. Given that table (see
:mod:`forward_sdk.rbac` for how to supply it; it is not shipped here) and the
account's roles from ``GET /users/current``, the question can be answered the way
Forward's enforcer answers it, without starting the call to find out.

An operation is named as Forward names it in a refusal: ``"NetworkOperation.
TRIGGER_SNAPSHOT_COLLECTION"`` for the enforcer's "Missing permission: ..." (the
bare ``"TRIGGER_SNAPSHOT_COLLECTION"`` works too).

The answer is ``True``, ``False`` or ``None``. ``None`` means it cannot be settled:
no table is configured, the roles could not be read or came back empty, the account
holds a Forward-internal role, or a role this table does not know. ``True`` means
the roles allow the operation. It does not mean the call will succeed: a licence
facet, an organization property or the state of the thing being changed can still
refuse it. Forward's own refusal stays the last word.
"""

from __future__ import annotations

from dataclasses import dataclass

from forward_sdk import _access, _role_table
from forward_sdk._access import NO_TABLE, Decision, OperationInfo  # noqa: F401
from forward_sdk._role_table import NETWORK_ROLES  # noqa: F401
from forward_sdk.errors import ForwardConfigurationError  # noqa: F401
from forward_sdk.rbac import RoleTable, discover

__all__ = ["Access", "Decision", "OperationInfo", "describe_operation", "operations"]


@dataclass(frozen=True, slots=True)
class Access(_access.Access):
    """The roles an account holds, and what they allow.

    Build one from ``client.access()``, or from a response you already have with
    :meth:`from_roles`. The rules are in :mod:`forward_sdk._access`; this finds the
    role table named by the environment when none is given.

    Attributes:
        org_roles: Org-level roles, usually ``{"ADMIN"}``.
        network_roles: Network id to the role held on it, as Forward reports it for
            the session. That includes roles inherited from access control groups
            (SAML and LDAP accounts only) and a workspace's inherited role when
            Forward's role propagation is on.
        system_roles: Forward-internal roles. Their reach is not modelled here, so
            any answer for an account that holds one is ``None``.
        network_id: The network ``can`` and ``at_least`` use when not given one.
        table: The role table to answer from; the one named by the environment when
            not given.
    """

    def _resolve_table(self) -> _role_table.RoleTable | None:
        return self.table or discover()


def describe_operation(operation: str, table: RoleTable | None = None) -> OperationInfo:
    """What Forward's access package says about one operation.

    Raises ``ValueError`` for a name Forward does not define, so a typo is not
    mistaken for "not allowed", and ``ForwardConfigurationError`` when no role table
    is configured.
    """
    return _access.describe_operation(operation, table or discover())


def operations(scope: str | None = None, table: RoleTable | None = None) -> list[OperationInfo]:
    """Every operation, optionally only ``"network"`` or ``"org"`` ones."""
    return _access.operations(scope, table or discover())
