"""``client.mcp``: the MCP client config, and the session-based client for Forward's MCP server."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardMcpError,
    ForwardNotFoundError,
    ForwardResponseError,
    ForwardServerError,
)
from forward_sdk.mcp import client_config, parse_messages
from tests.conftest import Recorder

pytestmark = pytest.mark.anyio

MCP = "/api/mcp"


def make_client(recorder: Recorder) -> AsyncForwardClient:
    # Closing the client ends an open MCP session.
    recorder.add("DELETE", MCP, httpx.Response(200))
    return AsyncForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


def posts(recorder: Recorder) -> list[httpx.Request]:
    return [r for r in recorder.requests if r.method == "POST"]


def sse(message: dict[str, Any], session: str | None = None) -> httpx.Response:
    """An answer as Forward sends it: one Server-Sent Event, even for a single call."""
    headers = {"content-type": "text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    return httpx.Response(
        200, text=f"event: message\ndata: {json.dumps(message)}\n\n", headers=headers
    )


def initialized(session: str = "S1", message_id: int = 1) -> list[httpx.Response]:
    return [
        sse(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "forward-enterprise", "version": "26.10.6"},
                },
            },
            session=session,
        ),
        httpx.Response(202),
    ]


class TestConfig:
    async def test_vscode_entry_carries_the_url_and_basic_auth(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            config = client.mcp.config(style="vscode")

        entry = config["servers"]["forward"]
        assert entry["type"] == "http"
        assert entry["url"] == "https://forward.test/api/mcp"
        scheme, token = entry["headers"]["Authorization"].split()
        assert scheme == "Basic" and base64.b64decode(token) == b"key:secret"
        assert recorder.requests == []

    @pytest.mark.parametrize(
        ("style", "outer", "has_type"),
        [("cursor", "mcpServers", False), ("claude", "mcpServers", True)],
    )
    async def test_other_styles(self, style: Any, outer: str, has_type: bool) -> None:
        entry = client_config("https://forward.test", "k", "s", name="fwd", style=style)[outer][
            "fwd"
        ]
        assert entry["url"] == "https://forward.test/api/mcp"
        assert ("type" in entry) is has_type

    async def test_generic_and_an_api_root_url(self) -> None:
        entry = client_config("https://forward.test/api/", "k", "s")
        assert entry["url"] == "https://forward.test/api/mcp"

    async def test_an_unknown_style_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown MCP config style"):
            client_config("https://forward.test", "k", "s", style="emacs")  # type: ignore[arg-type]

    async def test_no_credentials_is_refused(self) -> None:
        async with AsyncForwardClient("https://forward.test", rate_limit_rpm=None) as client:
            with pytest.raises(ForwardConfigurationError, match="username and password"):
                client.mcp.config()


def test_messages_parse_from_sse_or_json() -> None:
    assert parse_messages('event: message\ndata: {"id": 1}\n\n', "text/event-stream") == [{"id": 1}]
    assert parse_messages('{"id": 2}', "application/json") == [{"id": 2}]
    assert parse_messages("", "text/event-stream") == []


class TestSession:
    async def test_the_first_call_opens_a_session_and_later_calls_carry_it(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "get_paths",
                                "inputSchema": {"properties": {"networkId": {}, "srcIp": {}}},
                            }
                        ]
                    },
                }
            ),
        )
        async with make_client(recorder) as client:
            tools = await client.mcp.tools()

        sent = posts(recorder)
        init, notified, listed = (json.loads(r.content) for r in sent)
        assert init["method"] == "initialize" and "mcp-session-id" not in sent[0].headers
        assert notified == {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert listed["method"] == "tools/list"
        for request in sent[1:]:
            assert request.headers["mcp-session-id"] == "S1"
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
        assert "text/event-stream" in sent[0].headers["accept"]
        assert [t.name for t in tools] == ["get_paths"] and tools[0].arguments == [
            "networkId",
            "srcIp",
        ]
        assert client.mcp.server_info["name"] == "forward-enterprise"

    async def test_a_call_sends_the_client_network_and_returns_the_result(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": '{"hops": 3}'}]},
                }
            ),
        )
        async with make_client(recorder) as client:
            result = await client.mcp.call("get_paths", srcIp="192.0.2.1", dstIp="192.0.2.2")

        params = json.loads(posts(recorder)[-1].content)["params"]
        assert params == {
            "name": "get_paths",
            "arguments": {"networkId": "101", "srcIp": "192.0.2.1", "dstIp": "192.0.2.2"},
        }
        assert not result.is_error and result.data() == {"hops": 3}

    async def test_a_snapshot_is_sent_only_when_given(self, recorder: Recorder) -> None:
        recorder.add("POST", MCP, *initialized(), sse({"jsonrpc": "2.0", "id": 2, "result": {}}))
        async with make_client(recorder) as client:
            await client.mcp.call("get_vrf", network_id="7", snapshot_id="9")

        assert json.loads(posts(recorder)[-1].content)["params"]["arguments"] == {
            "networkId": "7",
            "snapshotId": "9",
        }

    async def test_a_tool_failure_is_a_result_not_an_exception(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "'prompt' is required"}],
                    },
                }
            ),
        )
        async with make_client(recorder) as client:
            result = await client.mcp.call("nqe", query="x")

        assert result.is_error and "prompt" in result.text

    async def test_a_json_rpc_error_raises(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "error": {"code": -32602, "message": "Unknown tool: nope"},
                }
            ),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardMcpError, match="Unknown tool") as caught:
                await client.mcp.call("nope")

        assert caught.value.code == -32602

    async def test_a_forgotten_session_is_reopened_once(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized("S1"),
            httpx.Response(404, json={"message": "Session not found"}),
            *initialized("S2", message_id=3),
            sse({"jsonrpc": "2.0", "id": 4, "result": {"tools": []}}),
        )
        async with make_client(recorder) as client:
            await client.mcp.tools()

        assert posts(recorder)[-1].headers["mcp-session-id"] == "S2"
        assert json.loads(recorder.requests[3].content)["method"] == "initialize"

    async def test_no_session_from_initialize_raises(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            sse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardMcpError, match="no session"):
                await client.mcp.tools()

    async def test_close_ends_the_session(self, recorder: Recorder) -> None:
        recorder.add("POST", MCP, *initialized(), sse({"jsonrpc": "2.0", "id": 2, "result": {}}))
        async with make_client(recorder) as client:
            await client.mcp.tools()
            await client.mcp.close()
            await client.mcp.close()  # nothing open: no request

        deletes = [r for r in recorder.requests if r.method == "DELETE"]
        assert len(deletes) == 1 and deletes[0].headers["mcp-session-id"] == "S1"

    async def test_closing_the_client_ends_the_session(self, recorder: Recorder) -> None:
        recorder.add("POST", MCP, *initialized(), sse({"jsonrpc": "2.0", "id": 2, "result": {}}))
        async with make_client(recorder) as client:
            await client.mcp.tools()

        assert recorder.requests[-1].method == "DELETE"
        assert recorder.requests[-1].headers["mcp-session-id"] == "S1"

    async def test_a_second_call_reuses_the_session(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse({"jsonrpc": "2.0", "id": 2, "result": {}}),
            sse({"jsonrpc": "2.0", "id": 3, "result": {}}),
        )
        async with make_client(recorder) as client:
            await client.mcp.call("get_vrf")
            await client.mcp.call("get_vlan")

        methods = [json.loads(r.content)["method"] for r in recorder.requests if r.method == "POST"]
        assert methods == ["initialize", "notifications/initialized", "tools/call", "tools/call"]

    async def test_a_second_404_is_raised_not_looped(self, recorder: Recorder) -> None:
        gone = httpx.Response(404, json={"message": "Session not found"})
        recorder.add("POST", MCP, *initialized("S1"), gone, *initialized("S2", message_id=3), gone)
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError):
                await client.mcp.tools()

        initializes = [
            r for r in recorder.requests if r.method == "POST" and b'"initialize"' in r.content
        ]
        assert len(initializes) == 2

    async def test_tools_follow_the_cursor_and_refuse_a_repeat(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "a"}], "nextCursor": "c1"},
                }
            ),
            sse({"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "b"}]}}),
        )
        async with make_client(recorder) as client:
            tools = await client.mcp.tools()

        assert [t.name for t in tools] == ["a", "b"]
        assert json.loads(recorder.requests[3].content)["params"] == {"cursor": "c1"}

    async def test_a_repeated_cursor_raises(self, recorder: Recorder) -> None:
        page = {"tools": [], "nextCursor": "same"}
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse({"jsonrpc": "2.0", "id": 2, "result": page}),
            sse({"jsonrpc": "2.0", "id": 3, "result": page}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardMcpError, match="repeated the cursor"):
                await client.mcp.tools()

    async def test_a_notification_or_request_before_the_answer_is_skipped(
        self, recorder: Recorder
    ) -> None:
        body = (
            'event: message\ndata: {"jsonrpc": "2.0", "method": "notifications/progress"}\n\n'
            'event: message\ndata: {"jsonrpc": "2.0", "id": 2, "method": "ping"}\n\n'
            "event: message\n"
            'data: {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "a"}]}}\n\n'
        )
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
        )
        async with make_client(recorder) as client:
            tools = await client.mcp.tools()

        assert [t.name for t in tools] == ["a"]

    async def test_a_server_error_on_a_call_is_not_retried(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", MCP, *initialized(), httpx.Response(503, json={"message": "busy"}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardServerError):
                await client.mcp.call("get_vrf")

        calls = [
            r for r in recorder.requests if r.method == "POST" and b'"tools/call"' in r.content
        ]
        assert len(calls) == 1

    async def test_a_body_that_is_neither_json_nor_sse_is_a_forward_error(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "POST",
            MCP,
            httpx.Response(200, text="<html>sign in</html>", headers={"content-type": "text/html"}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardResponseError):
                await client.mcp.tools()

    async def test_a_malformed_tool_is_a_forward_error(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            MCP,
            *initialized(),
            sse({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"description": "no name"}]}}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardResponseError):
                await client.mcp.tools()

    async def test_the_pinned_snapshot_is_sent(self, recorder: Recorder) -> None:
        recorder.add("POST", MCP, *initialized(), sse({"jsonrpc": "2.0", "id": 2, "result": {}}))
        recorder.add("DELETE", MCP, httpx.Response(200))
        async with AsyncForwardClient(
            "https://forward.test",
            username="key",
            password="secret",
            rate_limit_rpm=None,
            network_id="101",
            snapshot_id="55",
            transport=recorder.transport,
        ) as client:
            await client.mcp.call("get_vrf")

        sent = [json.loads(r.content) for r in recorder.requests if r.method == "POST"][-1]
        assert sent["params"]["arguments"]["snapshotId"] == "55"
