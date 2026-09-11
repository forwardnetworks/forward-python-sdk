"""Server-Sent Events, as Forward sends them.

Pure parsing, shared by the sync and async services. Forward writes each
message as an ``event:`` line naming the type, a ``data:`` line carrying compact
JSON, and a blank line; when a type carries no payload the data line is ``0``.
A comment line ``:ping`` arrives every few seconds as a keepalive and is not an
event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SseParser", "UserEvent"]


@dataclass(frozen=True, slots=True)
class UserEvent:
    """One event from the stream.

    Attributes:
        type: The event name, such as ``DEVICE_STATUSES_UPDATED`` or
            ``SNAPSHOT_PROGRESS_REPORT``. Forward adds names between releases,
            so this is a string rather than an enum; compare against the
            constants on :class:`~forward_sdk._async.services.events.AsyncUserEventsService`.
        data: The decoded payload. ``None`` for events that carry none, which
            Forward sends as a data line of ``0``.
        raw: The data line as received, for anything that does not decode.
    """

    type: str
    data: Any = None
    raw: str = ""


@dataclass
class SseParser:
    """Turn lines into events. Feed each line to :meth:`feed`.

    Holds the fields of the message being assembled; a blank line completes it.
    Multi-line ``data:`` fields are joined with newlines as the specification
    says, although Forward sends one line.
    """

    _event: str | None = None
    _data: list[str] = field(default_factory=list)

    def feed(self, line: str) -> UserEvent | None:
        """Consume one line; return an event when a message is complete."""
        line = line.rstrip("\r\n")
        if not line:
            return self._complete()
        if line.startswith(":"):
            return None  # a comment, which is how Forward sends its keepalive
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        # id and retry are not used by Forward; anything else is ignored.
        return None

    def _complete(self) -> UserEvent | None:
        if self._event is None and not self._data:
            return None
        raw = "\n".join(self._data)
        event_type = self._event or "message"
        self._event, self._data = None, []
        if raw in ("", "0"):
            return UserEvent(type=event_type, data=None, raw=raw)
        try:
            return UserEvent(type=event_type, data=json.loads(raw), raw=raw)
        except ValueError:
            return UserEvent(type=event_type, data=None, raw=raw)
