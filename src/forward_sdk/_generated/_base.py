"""Base types for generated models.

Hand-written, but lives beside the generated code because every generated model
inherits from :class:`ForwardModel`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ForwardModel(BaseModel):
    """Base class for every model generated from the Forward OpenAPI spec.

    ``extra="allow"`` is deliberate: Forward adds fields to responses between
    releases, and an SDK that rejected them would break on upgrade. Unknown
    fields are preserved and round-trip through :meth:`to_api`.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        validate_assignment=False,
        arbitrary_types_allowed=True,
    )

    def to_api(self) -> dict[str, Any]:
        """Serialize for a request body, using the API's own field names."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class OpenEnum(str, Enum):
    """A string enum that tolerates values a newer server may introduce.

    Forward adds enum members (new vendors, new device types, new snapshot
    states) in ordinary releases. A strict enum would raise on a value it has
    not been taught, turning an additive server change into a client outage.
    Unknown values become pseudo-members instead, so ``device.vendor`` still
    carries the server's string and comparison against known members simply
    returns False.
    """

    @classmethod
    def _missing_(cls, value: object) -> OpenEnum | None:
        if not isinstance(value, str):
            return None
        member = str.__new__(cls, value)
        member._name_ = value
        member._value_ = value
        # Deliberately not registered in _member_map_: unknown values must not
        # accumulate into the enum's public membership.
        return member

    def __str__(self) -> str:
        return str(self.value)
