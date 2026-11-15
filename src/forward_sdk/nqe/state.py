"""Typed state reads for before/after change checks: BGP, LLDP, interface admin, ACLs.

The same NQE queries and result columns a change gate compares before and
after a change, as typed rows, so a caller does not copy query text and column
names. ``client.nqe.bgp_sessions()`` and its siblings run them; this module
holds the queries and row types and has no I/O.

Mirrors the Go SDK v0.8.32 (``NQE.BGPSessions``, ``LLDPAdjacencies``,
``InterfaceAdmin``, ``ACLEntries``, ``ACLCounts``), whose queries were taken
verbatim from a change gate's Python original and proven on a production fleet.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

__all__ = [
    "ACL_ENTRIES_QUERY",
    "BGP_SESSIONS_QUERY",
    "BGP_SESSION_ESTABLISHED",
    "INTERFACE_ADMIN_QUERY",
    "INTERFACE_ADMIN_UP",
    "AclEntry",
    "BgpSession",
    "InterfaceAdmin",
    "LldpAdjacency",
    "cell_text",
    "lldp_query",
]

#: Every BGP neighbor of every network instance, with its session state.
BGP_SESSIONS_QUERY = """foreach device in network.devices
foreach ni in device.networkInstances
foreach protocol in ni.protocols
where isPresent(protocol.bgp)
foreach neighbor in protocol.bgp.neighbors
select {
  Device: device.name, VRF: ni.name,
  NeighborIP: neighbor.neighborAddress, RemoteAS: neighbor.peerAS,
  SessionState: neighbor.sessionState
}"""

#: Each interface's administrative state. ``toString()`` on Forward's enum gives
#: ``"AdminStatus.UP"``, not ``"UP"``: a check that compared with ``"UP"`` would
#: match nothing and pass every shutdown.
INTERFACE_ADMIN_QUERY = """foreach device in network.devices
foreach interface in device.interfaces
select {
  Device: device.name, Interface: interface.name,
  AdminStatus: toString(interface.adminStatus)
}"""

#: One row per ACL entry.
ACL_ENTRIES_QUERY = """foreach device in network.devices
foreach acl in device.aclEntries
select { Device: device.name, AclName: acl.name, Action: toString(acl.action) }"""

#: The ``AdminStatus`` text of an administratively up interface, as the read returns it.
INTERFACE_ADMIN_UP = "AdminStatus.UP"
#: The ``SessionState`` of an established BGP session.
BGP_SESSION_ESTABLISHED = "ESTABLISHED"

_LLDP_TAIL = """foreach neighbor in interface.lldp.neighbors
select {
  Device: device.name, LocalIntf: interface.name,
  RemoteSys: neighbor.deviceName, RemotePort: neighbor.portName
}"""


def _literal(value: str) -> str:
    if not value or any(c in value for c in '"\\\n\r'):
        raise ValueError(
            f"{value!r} cannot be placed in an NQE string literal "
            "(empty, or has a quote, backslash or newline)"
        )
    return f'"{value}"'


def lldp_query(devices: Sequence[str] = (), skip_interfaces: Sequence[str] = ()) -> str:
    """The LLDP read, optionally limited to ``devices`` and without ``skip_interfaces``.

    Names are matched exactly. A name with a double quote, backslash or newline
    is refused rather than allowed to break out of the query's string literal.
    """
    if isinstance(devices, str) or isinstance(skip_interfaces, str):
        raise ValueError("devices and skip_interfaces take a list of names")
    lines = ["foreach device in network.devices"]
    if devices:
        lines.append("where " + " || ".join(f"device.name == {_literal(d)}" for d in devices))
    lines.append("foreach interface in device.interfaces")
    if skip_interfaces:
        lines.append(
            "where " + " && ".join(f"interface.name != {_literal(i)}" for i in skip_interfaces)
        )
    return "\n".join(lines) + "\n" + _LLDP_TAIL


class _Row(BaseModel):
    """A row of one of the fixed queries above.

    A null cell reads as ``""`` for text columns, as Go decodes it, so one
    vendor leaving a name out does not fail the whole read.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _null_text(cls, value: Any, info: Any) -> Any:
        field = cls.model_fields.get(info.field_name)
        if value is None and field is not None and field.annotation is str:
            return ""
        return value


class BgpSession(_Row):
    """One BGP neighbor. Identity: device, VRF and neighbor IP."""

    device: str = Field(alias="Device")
    vrf: str = Field(alias="VRF")
    neighbor_ip: str = Field(alias="NeighborIP")
    #: Text, because NQE can return a 32-bit AS as a number or as text.
    remote_as: str | None = Field(default=None, alias="RemoteAS")
    session_state: str | None = Field(default=None, alias="SessionState")

    @field_validator("remote_as", mode="before")
    @classmethod
    def _as_text(cls, value: Any) -> Any:
        return None if value is None else str(value)

    @property
    def key(self) -> tuple[str, str, str]:
        """The identity a before/after diff matches on."""
        return (self.device, self.vrf, self.neighbor_ip)

    @property
    def established(self) -> bool:
        """Case-insensitive, as in Go: the live text of a non-empty read is not yet seen."""
        return (self.session_state or "").upper() == BGP_SESSION_ESTABLISHED


class LldpAdjacency(_Row):
    """One LLDP neighbor. Identity: all four fields."""

    device: str = Field(alias="Device")
    local_intf: str = Field(alias="LocalIntf")
    remote_sys: str | None = Field(default=None, alias="RemoteSys")
    remote_port: str | None = Field(default=None, alias="RemotePort")

    @property
    def key(self) -> tuple[str, str, str | None, str | None]:
        return (self.device, self.local_intf, self.remote_sys, self.remote_port)


class InterfaceAdmin(_Row):
    """One interface's administrative state. Identity: device and interface."""

    device: str = Field(alias="Device")
    interface: str = Field(alias="Interface")
    #: Forward's enum text, ``"AdminStatus.UP"`` or ``"AdminStatus.DOWN"``; use :attr:`up`.
    admin_status: str | None = Field(default=None, alias="AdminStatus")

    @property
    def key(self) -> tuple[str, str]:
        return (self.device, self.interface)

    @property
    def up(self) -> bool:
        return self.admin_status == INTERFACE_ADMIN_UP


class AclEntry(_Row):
    """One row of the ACL entry read: one per entry, so device and ACL name can repeat.

    ``acl_name`` is the entry's own name (NQE's ``aclEntry.name``), which a
    vendor may leave empty or share across entries. ``uuid`` and
    ``description`` are optional columns a query may add; ``uuid`` is the only
    per-entry identifier Forward's data model has, and only for vendors that
    give one. From :meth:`acl_entries_with`, ``row`` holds every column the
    query selected and ``identity`` the key columns' values as text.
    """

    device: str = Field(alias="Device")
    acl_name: str = Field(alias="AclName")
    #: Forward's enum text, for example ``"AclAction.PERMIT"``.
    action: str | None = Field(default=None, alias="Action")
    uuid: str | None = Field(default=None, alias="UUID")
    description: str | None = Field(default=None, alias="Description")
    # Private, so the entry stays hashable (a dict field would not be) and a
    # query column named "row" or "identity" cannot fill them.
    _row: dict[str, Any] = PrivateAttr(default_factory=dict)
    _identity: tuple[str, ...] = PrivateAttr(default=())

    @property
    def row(self) -> dict[str, Any]:
        """Every column the query selected (from :meth:`acl_entries_with`; empty otherwise)."""
        return self._row

    @property
    def identity(self) -> tuple[str, ...]:
        """The key columns' values as text (from :meth:`acl_entries_with` with ``key_columns``)."""
        return self._identity

    def _attach(self, row: dict[str, Any], identity: tuple[str, ...]) -> AclEntry:
        self._row = row
        self._identity = identity
        return self

    @property
    def acl(self) -> tuple[str, str]:
        """The ACL this entry belongs to."""
        return (self.device, self.acl_name)

    @property
    def key(self) -> tuple[str, str, str]:
        """Device, ACL name and UUID: unique only if the query selects an identifying column."""
        return (self.device, self.acl_name, self.uuid or "")


def cell_text(value: Any) -> str:
    """One NQE value as text: a string as itself, anything else as its JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
