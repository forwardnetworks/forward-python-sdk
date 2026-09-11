# Generated from src/forward_sdk/_async/services/events.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""The calling user's live event stream.

Unpublished. Forward pushes Server-Sent Events for the things a user is
watching: per-device collection progress, snapshot processing stages,
change-set updates, collector tasks, connectivity test results and more. It
is the only source of per-device collection detail; polling collector tasks
gives the task, not the device.

The stream never ends on its own. Iterate it inside a task you can cancel, or
stop when you have seen what you were waiting for.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator

from forward_sdk._ops import events as ops
from forward_sdk._sync.services._base import Service
from forward_sdk.events import SseParser, UserEvent

__all__ = ["UserEvent", "UserEventsService"]


class UserEventsService(Service):
    """Subscribe to the calling user's events."""

    #: Event names Forward sends, as of the build this was written against. It
    #: adds names between releases, so :attr:`UserEvent.type` is a string and
    #: an unknown name is delivered rather than dropped.
    DEVICE_STATUSES_UPDATED = "DEVICE_STATUSES_UPDATED"
    SNAPSHOT_PROGRESS_REPORT = "SNAPSHOT_PROGRESS_REPORT"
    SNAPSHOT_PROCESS_EVENT = "SNAPSHOT_PROCESS_EVENT"
    CONNECTIVITY_TEST_RESULT = "CONNECTIVITY_TEST_RESULT"
    CLOUD_CONNECTIVITY_TEST_RESULT = "CLOUD_CONNECTIVITY_TEST_RESULT"
    CHECKS_UPDATED = "CHECKS_UPDATED"
    CHECK_COMPUTATION_STARTED = "CHECK_COMPUTATION_STARTED"
    CHANGE_SET_UPDATED = "CHANGE_SET_UPDATED"
    CHANGE_SET_DELETED = "CHANGE_SET_DELETED"
    COLLECTOR_TASK_QUEUED = "COLLECTOR_TASK_QUEUED"
    COLLECTOR_TASK_STARTED = "COLLECTOR_TASK_STARTED"
    COLLECTOR_TASK_FINISHED = "COLLECTOR_TASK_FINISHED"
    COLLECTOR_ONLINE = "COLLECTOR_ONLINE"
    COLLECTOR_OFFLINE = "COLLECTOR_OFFLINE"
    NQE_PROGRESS = "NQE_PROGRESS"
    NQE_DIFF_COMPUTE_COMPLETED = "NQE_DIFF_COMPUTE_COMPLETED"
    NQE_LIBRARY_COMMIT = "NQE_LIBRARY_COMMIT"
    NOTIFICATION = "NOTIFICATION"
    SESSION_INVALIDATED = "SESSION_INVALIDATED"

    def stream(self, *, types: Collection[str] | None = None) -> Iterator[UserEvent]:
        """Yield events as Forward sends them, until the caller stops.

        Args:
            types: Deliver only these event names. The stream itself cannot be
                filtered, so everything is still received; this saves the
                caller a comparison, not bandwidth.

        Keepalive comments are consumed here and never yielded. A dropped
        connection ends the iteration with a transport error; there is no
        resumption, since Forward keeps no cursor, so a caller that must not
        miss an event re-reads the state it cares about after reconnecting.
        """
        wanted = set(types) if types else None
        parser = SseParser()
        with self._transport.stream(ops.user_events()) as response:
            for line in response.iter_lines():
                event = parser.feed(line)
                if event is None:
                    continue
                if wanted is None or event.type in wanted:
                    yield event
