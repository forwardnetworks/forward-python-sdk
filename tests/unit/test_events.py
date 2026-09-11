"""Parsing Forward's Server-Sent Events."""

from __future__ import annotations

from forward_sdk.events import SseParser, UserEvent


def feed_all(lines: list[str]) -> list[UserEvent]:
    parser = SseParser()
    events = []
    for line in lines:
        event = parser.feed(line)
        if event is not None:
            events.append(event)
    return events


class TestSseParser:
    def test_an_event_with_json_data(self) -> None:
        events = feed_all(["event:CHANGE_SET_DELETED", 'data:["CHG-278"]', ""])
        assert events == [UserEvent(type="CHANGE_SET_DELETED", data=["CHG-278"], raw='["CHG-278"]')]

    def test_forward_sends_zero_for_no_payload(self) -> None:
        events = feed_all(["event:NQE_LIBRARY_COMMIT", "data:0", ""])
        assert events[0].type == "NQE_LIBRARY_COMMIT"
        assert events[0].data is None

    def test_keepalive_comments_are_not_events(self) -> None:
        """Forward sends ':ping' every few seconds; nothing should surface."""
        assert feed_all([":ping", "", ":ping", ""]) == []

    def test_a_space_after_the_colon_is_optional(self) -> None:
        events = feed_all(["event: X", 'data: {"a": 1}', ""])
        assert events[0].data == {"a": 1}

    def test_multi_line_data_is_joined(self) -> None:
        events = feed_all(["event:X", 'data:{"a":', "data:1}", ""])
        assert events[0].data == {"a": 1}

    def test_undecodable_data_is_kept_raw(self) -> None:
        events = feed_all(["event:X", "data:not json", ""])
        assert events[0].data is None
        assert events[0].raw == "not json"

    def test_two_messages_back_to_back(self) -> None:
        events = feed_all(["event:A", "data:1", "", "event:B", "data:2", ""])
        assert [(e.type, e.data) for e in events] == [("A", 1), ("B", 2)]

    def test_line_endings_are_stripped(self) -> None:
        events = feed_all(["event:A\r\n", "data:1\r\n", "\r\n"])
        assert events[0].data == 1
