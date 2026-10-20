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

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.rbac import NETWORK_ROLES, RoleTable, discover

__all__ = ["Access", "Decision", "OperationInfo", "describe_operation", "operations"]

_NETWORK_PREFIX = "NetworkOperation."
_ORG_PREFIX = "OrgOperation."

NO_TABLE = (
    "no role table is configured, so the roles cannot be checked "
    "(set FORWARD_RBAC_TABLE or FORWARD_SOURCE; see docs/access.md)"
)


@dataclass(frozen=True, slots=True)
class OperationInfo:
    """One operation, as Forward's access package declares it."""

    name: str
    #: ``"network"`` (checked against one network) or ``"org"`` (checked org-wide).
    scope: str
    #: ``"READ"`` or ``"WRITE"``.
    access_type: str
    #: The licence facet the operation needs, such as ``BASE`` or ``PREDICT``.
    license_facet: str
    #: The lowest network role that holds it, or ``None`` when only an org admin does.
    lowest_network_role: str | None
    doc: str


def _need(table: RoleTable | None) -> RoleTable:
    resolved = table or discover()
    if resolved is None:
        raise ForwardConfigurationError(NO_TABLE)
    return resolved


def _lookup(operation: str, table: RoleTable) -> OperationInfo:
    name = operation.strip()
    scopes: tuple[str, ...]
    if name.startswith(_NETWORK_PREFIX):
        name, scopes = name.removeprefix(_NETWORK_PREFIX), ("network",)
    elif name.startswith(_ORG_PREFIX):
        name, scopes = name.removeprefix(_ORG_PREFIX), ("org",)
    else:
        scopes = ("network", "org")
    for scope in scopes:
        rows = table.network_operations if scope == "network" else table.org_operations
        if name in rows:
            row = rows[name]
            return OperationInfo(
                name, scope, row.access_type, row.license_facet, row.lowest_network_role, row.doc
            )
    known = [*table.network_operations, *table.org_operations]
    close = difflib.get_close_matches(name, known, n=3)
    hint = f"; did you mean {', '.join(close)}?" if close else ""
    raise ValueError(f"unknown operation {operation!r}{hint}")


def describe_operation(operation: str, table: RoleTable | None = None) -> OperationInfo:
    """What Forward's access package says about one operation.

    Raises ``ValueError`` for a name Forward does not define, so a typo is not
    mistaken for "not allowed", and ``ForwardConfigurationError`` when no role table
    is configured.
    """
    return _lookup(operation, _need(table))


def operations(scope: str | None = None, table: RoleTable | None = None) -> list[OperationInfo]:
    """Every operation, optionally only ``"network"`` or ``"org"`` ones."""
    resolved = _need(table)
    found: list[OperationInfo] = []
    for kind, rows in (
        ("network", resolved.network_operations),
        ("org", resolved.org_operations),
    ):
        if scope in (None, kind):
            found.extend(
                OperationInfo(n, kind, r.access_type, r.license_facet, r.lowest_network_role, r.doc)
                for n, r in rows.items()
            )
    return found


def _as_roles(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


@dataclass(frozen=True, slots=True)
class Decision:
    """An answer about one operation, with the reason and the role it rested on."""

    #: ``True`` the roles allow it, ``False`` they do not, ``None`` they cannot settle it.
    allowed: bool | None
    reason: str
    #: The role the answer relied on, when it relied on one.
    role: str | None
    #: What Forward says about the operation; ``None`` when there was no table to ask.
    operation: OperationInfo | None

    def __bool__(self) -> bool:
        raise TypeError(
            "a Decision has three outcomes; test .allowed, which may be None, not the decision"
        )


@dataclass(frozen=True, slots=True)
class Access:
    """The roles an account holds, and what they allow.

    Build one from ``client.access()``, or from a response you already have with
    :meth:`from_roles`.

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

    org_roles: frozenset[str] = frozenset()
    network_roles: Mapping[str, str] = field(default_factory=dict)
    system_roles: frozenset[str] = frozenset()
    network_id: str | None = None
    known: bool = True
    #: Why the roles could not be read, when they could not. Every answer is then ``None``.
    unreadable: str = ""
    table: RoleTable | None = None
    #: The Forward support account impersonating this one, when the session is an impersonation.
    impersonator: str | None = None
    #: Other orgs a Forward support account may reach. Present only for support accounts.
    supported_orgs: frozenset[str] = frozenset()

    @classmethod
    def unreadable_because(
        cls, why: str, network_id: str | None = None, table: RoleTable | None = None
    ) -> Access:
        """An ``Access`` for roles that could not be read: every answer is ``None``."""
        return cls(network_id=network_id, known=False, unreadable=why, table=table)

    @classmethod
    def from_roles(
        cls,
        roles: Any,
        network_id: str | None = None,
        table: RoleTable | None = None,
        *,
        impersonator: str | None = None,
    ) -> Access:
        """From the ``roles`` of ``GET /users/current`` (a model or a plain dict).

        Missing or empty roles give an ``Access`` whose answers are all ``None``:
        an empty answer is not a refusal, it is not knowing. So are the answers for
        an impersonated session (``impersonator``: the support account's name, from
        the same response) and for a Forward support account, whose reach the roles
        alone do not describe.
        """
        if roles is None:
            return cls(network_id=network_id, known=False, table=table, impersonator=impersonator)
        if isinstance(roles, Mapping):
            raw: Mapping[str, Any] = roles
        else:
            raw = {
                "org": getattr(roles, "org", None),
                "network": getattr(roles, "network", None),
                **(getattr(roles, "model_extra", None) or {}),
            }
        network = {str(k): str(v) for k, v in (raw.get("network") or {}).items()}
        org = _as_roles(raw.get("org"))
        system = _as_roles(raw.get("system"))
        supported = _as_roles(raw.get("supportedOrgIds"))
        return cls(
            org_roles=org,
            network_roles=network,
            system_roles=system,
            network_id=network_id,
            known=bool(org or network or system),
            table=table,
            impersonator=impersonator,
            supported_orgs=supported,
        )

    @property
    def org_admin(self) -> bool:
        return "ADMIN" in self.org_roles

    def role_on(self, network_id: str | None = None) -> str | None:
        """The network role held on a network, or ``None`` when there is none."""
        resolved = network_id if network_id is not None else self.network_id
        if resolved is None:
            raise ValueError("no network id: pass one, or build the Access with network_id=")
        return self.network_roles.get(str(resolved))

    def at_least(self, role: str, network_id: str | None = None) -> bool | None:
        """Whether the account's role on a network is ``role`` or a higher one.

        An org admin counts as holding every network role. Needs no role table.
        """
        if role not in NETWORK_ROLES:
            raise ValueError(f"unknown network role {role!r}; known: {', '.join(NETWORK_ROLES)}")
        if self.org_admin:
            return True
        held = self.role_on(network_id)
        if held is None:
            return None if self._cannot_tell() else False
        if held not in NETWORK_ROLES:
            return None
        return NETWORK_ROLES.index(held) >= NETWORK_ROLES.index(role)

    def can(self, operation: str, network_id: str | None = None) -> bool | None:
        """Whether the account's roles allow an operation: ``True``, ``False`` or ``None``.

        The short form of :meth:`may`, which also says why.
        """
        return self.may(operation, network_id).allowed

    def may(self, operation: str, network_id: str | None = None) -> Decision:
        """Whether the account's roles allow an operation, and why.

        A network operation is checked against one network (``network_id``, else
        the one this was built with). An org operation is checked org-wide: Forward
        allows it when the org role does or when any network role the account
        holds does.

        Follows ``OrgAuthority`` and ``NetworkAuthority`` in Forward's source: an
        org admin may do everything, an org read-only role may do every read, and
        a network role holds what its table lists. ``allowed`` is ``None`` when the
        roles cannot settle it; see the module docstring for what ``True`` does not
        promise. An operation the table does not define raises ``ValueError``.
        """
        table = self.table or discover()
        if table is None:
            return Decision(None, NO_TABLE, None, None)
        info = _lookup(operation, table)
        if self.org_admin:
            return Decision(True, "an org ADMIN holds every operation", "ADMIN", info)
        if "READ_ONLY" in self.org_roles and info.access_type == "READ":
            return Decision(True, "an org READ_ONLY role holds every read", "READ_ONLY", info)
        lowest = info.lowest_network_role
        if info.scope == "network":
            where = network_id if network_id is not None else self.network_id
            held = self.role_on(network_id)
            roles = [] if held is None else [held]
            place = f"network {where}"
        else:
            roles = list(self.network_roles.values())
            place = "any network"
        if lowest is not None:
            for role in roles:
                if role in NETWORK_ROLES and NETWORK_ROLES.index(role) >= NETWORK_ROLES.index(
                    lowest
                ):
                    return Decision(
                        True,
                        f"{role} on {place} holds it (lowest role that does: {lowest})",
                        role,
                        info,
                    )
        unrecognised = [role for role in roles if role not in NETWORK_ROLES]
        if unrecognised:
            return Decision(
                None,
                f"role {unrecognised[0]!r} on {place} is not one this table knows",
                None,
                info,
            )
        if self.unreadable:
            reason = f"the account's roles could not be read: {self.unreadable}"
            return Decision(None, reason, None, info)
        if not self.known:
            return Decision(None, "Forward returned no roles for the account", None, info)
        if self.system_roles:
            reason = "the account holds a Forward-internal role, which is not modelled"
            return Decision(None, reason, None, info)
        if self.supported_orgs:
            reason = "a Forward support account may reach orgs and operations its roles do not show"
            return Decision(None, reason, None, info)
        if self.impersonator:
            reason = (
                f"the session is {self.impersonator} impersonating this account, "
                "which gains operations the roles do not show"
            )
            return Decision(None, reason, None, info)
        if lowest is None:
            return Decision(False, "no network role holds it; only an org ADMIN does", None, info)
        if not roles:
            return Decision(False, f"the account holds no role on {place}", None, info)
        best = max(roles, key=NETWORK_ROLES.index)
        reason = f"{best} on {place} is below {lowest}, the lowest role that holds it"
        return Decision(False, reason, best, info)

    def _cannot_tell(self) -> bool:
        """Roles that say nothing, that could not be read, or that belong to Forward itself."""
        return (
            not self.known
            or bool(self.system_roles)
            or bool(self.supported_orgs)
            or bool(self.impersonator)
            or bool(self.unreadable)
        )
