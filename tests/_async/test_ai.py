"""Forward AI: asking questions, polling for answers, and being refused.

The refusal path matters as much as the success one: most organizations do not
have Forward AI, and a caller needs to distinguish "not available here" from
"something broke".
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import ForwardPermissionError, ForwardTimeoutError
from tests.conftest import Recorder, error_response, json_response

pytestmark = pytest.mark.anyio

CHATS = "/api/ai-chats"
CHAT = "/api/ai-chats/42"
MESSAGES = "/api/ai-chats/42/messages"

ANSWERED = {
    "id": "42",
    "networkId": "101",
    "snapshotId": "12345",
    "status": "DONE",
    "name": "Why is nyc-fw01 dropping traffic?",
}
THINKING = {**ANSWERED, "status": "PROCESSING", "name": None}


def make_client(recorder: Recorder, **overrides: Any) -> AsyncForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "network_id": "101",
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return AsyncForwardClient("https://forward.test", **settings)


def message(summary: str = "Blocked by an ACL.", **extra: Any) -> dict[str, Any]:
    return {
        "id": "7",
        "prompt": "Why is nyc-fw01 dropping traffic?",
        "toolCalls": [{"type": "GET_PATHS"}],
        "answer": {"summary": summary, "keyInsights": ["ACL denies 10.2.0.0/16"]},
        **extra,
    }


class TestAsking:
    async def test_start_returns_immediately_while_forward_thinks(self, recorder: Recorder) -> None:
        recorder.add("POST", CHATS, json_response(THINKING, status=201))
        async with make_client(recorder) as client:
            chat = await client.ai.start("Why is nyc-fw01 dropping traffic?")

        assert chat.id == "42"
        assert chat.status == "PROCESSING"
        assert chat.is_done is False
        assert recorder.body_for() == {"prompt": "Why is nyc-fw01 dropping traffic?"}
        assert recorder.query_for()["networkId"] == ["101"]

    async def test_snapshot_omitted_means_latest_processed(self, recorder: Recorder) -> None:
        recorder.add("POST", CHATS, json_response(THINKING, status=201))
        async with make_client(recorder) as client:
            await client.ai.start("q")

        assert "snapshotId" not in recorder.query_for()

    async def test_wait_polls_until_answered(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", CHATS, json_response(THINKING, status=201))
        recorder.add("GET", CHAT, json_response(THINKING), json_response(ANSWERED))
        async with make_client(recorder) as client:
            chat = await client.ai.start("q")
            await chat.wait()

        assert chat.is_done
        assert recorder.count("GET", CHAT) == 2

    async def test_wait_times_out_without_stopping_forward(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", CHATS, json_response(THINKING, status=201))
        recorder.add("GET", CHAT, json_response(THINKING))
        async with make_client(recorder) as client:
            chat = await client.ai.start("q")
            with pytest.raises(ForwardTimeoutError, match="still working"):
                await chat.wait(timeout=0)

    async def test_ask_starts_waits_and_returns_the_answer(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", CHATS, json_response(ANSWERED, status=201))
        recorder.add("GET", MESSAGES, json_response({"messages": [message()]}))
        async with make_client(recorder) as client:
            answer = await client.ai.ask("Why is nyc-fw01 dropping traffic?")

        assert answer is not None
        assert answer.answer is not None
        assert answer.answer.summary == "Blocked by an ACL."
        assert [str(call.type) for call in answer.tool_calls or []] == ["GET_PATHS"]

    async def test_a_conversation_stays_on_one_snapshot(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Follow-ups are answered against the same model as the first question."""
        recorder.add("POST", CHATS, json_response(ANSWERED, status=201))
        recorder.add("POST", MESSAGES, httpx.Response(202))
        recorder.add("GET", CHAT, json_response(ANSWERED))
        recorder.add("GET", MESSAGES, json_response({"messages": [message()]}))

        async with make_client(recorder) as client:
            chat = await client.ai.start("first")
            assert chat.snapshot_id == "12345"
            await chat.ask_and_wait("and what about the return path?")
            assert chat.snapshot_id == "12345"

        assert recorder.body_for(index=1) == {"prompt": "and what about the return path?"}

    async def test_asking_marks_the_chat_as_working_again(self, recorder: Recorder) -> None:
        recorder.add("POST", CHATS, json_response(ANSWERED, status=201))
        recorder.add("POST", MESSAGES, httpx.Response(202))
        async with make_client(recorder) as client:
            chat = await client.ai.start("first")
            assert chat.is_done
            await chat.ask("follow up")
            assert chat.is_done is False

    async def test_out_of_scope_answers_are_readable(self, recorder: Recorder) -> None:
        """Forward declining to answer is an answer, not an error."""
        recorder.add("POST", CHATS, json_response(ANSWERED, status=201))
        recorder.add(
            "GET",
            MESSAGES,
            json_response(
                {"messages": [message("I can only answer questions about this network.")]}
            ),
        )
        async with make_client(recorder) as client:
            answer = await client.ai.ask("what is the weather")

        assert answer is not None and answer.answer is not None
        assert "only answer questions" in (answer.answer.summary or "")


class TestManagingChats:
    async def test_list_chats(self, recorder: Recorder) -> None:
        recorder.add("GET", CHATS, json_response({"chats": [ANSWERED]}))
        async with make_client(recorder) as client:
            chats = await client.ai.list()

        assert [c.id for c in chats] == ["42"]

    async def test_messages_can_be_filtered_by_time(self, recorder: Recorder) -> None:
        recorder.add("GET", CHAT, json_response(ANSWERED))
        recorder.add("GET", MESSAGES, json_response({"messages": []}))
        async with make_client(recorder) as client:
            chat = await client.ai.get("42")
            await chat.messages(since="2026-09-07T09:00:00Z")

        assert recorder.query_for()["since"] == ["2026-09-07T09:00:00Z"]

    async def test_rename_and_delete(self, recorder: Recorder) -> None:
        recorder.add("GET", CHAT, json_response(ANSWERED))
        recorder.add("PATCH", CHAT, json_response({**ANSWERED, "name": "Renamed"}))
        recorder.add("DELETE", CHAT, httpx.Response(204))
        async with make_client(recorder) as client:
            chat = await client.ai.get("42")
            renamed = await chat.rename("Renamed")
            assert renamed.name == "Renamed"
            await chat.delete()

    async def test_transcript_is_returned_as_text(self, recorder: Recorder) -> None:
        recorder.add("GET", CHAT, json_response(ANSWERED))
        recorder.add(
            "GET",
            "/api/ai-chats/42/transcript",
            httpx.Response(200, text="# Chat\n\nBlocked by an ACL.\n"),
        )
        async with make_client(recorder) as client:
            chat = await client.ai.get("42")
            rendered = await chat.transcript()

        assert rendered.startswith("# Chat")
        assert recorder.query_for()["format"] == ["MARKDOWN"]


class TestUnavailable:
    async def test_an_organization_without_forward_ai_is_refused(self, recorder: Recorder) -> None:
        """The common case, since Forward AI is not part of every deployment.

        The SDK passes Forward's own sentence through rather than inventing a
        status of its own, which would be a second vocabulary to keep in step.
        """
        recorder.add(
            "POST",
            CHATS,
            error_response(403, "AI_ALLOWED is off for your organization"),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardPermissionError) as caught:
                await client.ai.start("why?")

        error = caught.value
        assert error.status == 403
        assert error.error_info is not None
        assert "AI_ALLOWED" in (error.error_info.message or "")

    async def test_starting_a_chat_is_not_retried(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """Retrying after a response would leave a second chat behind."""
        recorder.add("POST", CHATS, httpx.ReadTimeout("slow"))
        async with make_client(recorder) as client:
            with pytest.raises(Exception, match="failed"):
                await client.ai.start("why?")

        assert recorder.count("POST", CHATS) == 1
