"""Request builder for the user event stream."""

from __future__ import annotations

from forward_sdk._ops import RequestSpec, op, spec_for


@op("streamUserEvents")
def user_events() -> RequestSpec:
    return spec_for("streamUserEvents", accept="text/event-stream", stream=True)
