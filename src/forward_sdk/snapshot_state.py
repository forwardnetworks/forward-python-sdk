"""A snapshot's processing state before and after a request that changes it.

Pure and shared by the sync and async services, so both clients return the
same class.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["SnapshotStateChange"]


@dataclass(frozen=True, slots=True)
class SnapshotStateChange:
    """What invalidating or reprocessing a snapshot did to its state.

    Attributes:
        previous_state: The state before the request, such as ``PROCESSED``.
        state: The state Forward reported once the request was accepted.
            Processing continues after this returns; it is not the final state.
    """

    previous_state: str | None
    state: str | None

    @classmethod
    def from_payload(cls, payload: Any) -> SnapshotStateChange:
        data = payload if isinstance(payload, Mapping) else {}
        previous, current = data.get("previousState"), data.get("state")
        return cls(
            previous_state=None if previous is None else str(previous),
            state=None if current is None else str(current),
        )

    def to_api(self) -> dict[str, Any]:
        return {"previousState": self.previous_state, "state": self.state}
