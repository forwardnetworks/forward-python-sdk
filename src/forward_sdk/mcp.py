"""Forward's MCP server: client configuration, and the shapes ``client.mcp`` returns.

Forward serves its read-only AI tools over the Model Context Protocol at
``<url>/api/mcp`` (streamable HTTP). :func:`client_config` writes the entry an
MCP client (VS Code, Cursor, Claude Code) needs, from the same settings
``ForwardClient`` uses, so a tool never builds the Basic auth header by hand.
``client.mcp.tools()`` and ``client.mcp.call()`` talk to the server directly.

Seen live on 26.10.6 (2026-11-15, by the AutoCon6 workshop tooling): the server
is session-based, though Forward's documentation calls it stateless, and it
answers as Server-Sent Events.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal

from pydantic import Field

from forward_sdk._generated._base import ForwardModel
from forward_sdk._http import normalize_base_url
from forward_sdk.errors import ForwardResponseError
from forward_sdk.events import SseParser

__all__ = [
    "PROTOCOL_VERSION",
    "McpStyle",
    "McpTool",
    "McpToolResult",
    "client_config",
    "parse_messages",
]

#: The MCP protocol version this client asks for. Forward's server (MCP Java
#: SDK 2.0.1) answers with the version it will speak, which is then used.
PROTOCOL_VERSION = "2025-06-18"

McpStyle = Literal["vscode", "cursor", "claude", "generic"]


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def client_config(
    url: str,
    username: str,
    password: str,
    *,
    name: str = "forward",
    style: McpStyle = "generic",
) -> dict[str, Any]:
    """An MCP client entry for Forward's server at ``url`` (``https://host`` or its ``/api`` root).

    ``style`` picks the file format: ``"vscode"`` (``.vscode/mcp.json``,
    ``{"servers": {name: ...}}``), ``"cursor"`` (``mcp.json``,
    ``{"mcpServers": {name: ...}}``), ``"claude"`` (Claude Code's ``.mcp.json``,
    the same with ``"type": "http"``), or ``"generic"`` (just the server entry).

    The entry holds the credential in its ``Authorization`` header: keep the
    file out of version control.
    """
    root = normalize_base_url(url)
    entry: dict[str, Any] = {
        "type": "http",
        "url": f"{root}/mcp",
        "headers": {"Authorization": _basic(username, password)},
    }
    if style == "generic":
        return entry
    if style == "vscode":
        return {"servers": {name: entry}}
    if style == "claude":
        return {"mcpServers": {name: entry}}
    if style == "cursor":
        return {"mcpServers": {name: {key: entry[key] for key in ("url", "headers")}}}
    raise ValueError(f"unknown MCP config style {style!r}: vscode, cursor, claude or generic")


def parse_messages(text: str, content_type: str | None) -> list[Any]:
    """The JSON-RPC messages in one response, whether sent as JSON or as Server-Sent Events."""
    if not text.strip():
        return []
    if "text/event-stream" not in (content_type or ""):
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise ForwardResponseError(
                "Forward's MCP server answered with neither JSON nor Server-Sent Events",
                payload=text[:2000],
                model_name="JSON-RPC message",
            ) from error
        return payload if isinstance(payload, list) else [payload]
    parser = SseParser()
    out: list[Any] = []
    for line in [*text.splitlines(), ""]:
        event = parser.feed(line)
        if event is not None and event.data is not None:
            out.append(event.data)
    return out


class McpTool(ForwardModel):
    """One tool the server offers, from ``tools/list``."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="inputSchema")

    @property
    def arguments(self) -> list[str]:
        """The argument names the tool's schema declares."""
        return list((self.input_schema.get("properties") or {}).keys())


class McpToolResult(ForwardModel):
    """A ``tools/call`` result. ``is_error`` is a tool-level failure the server reported."""

    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = Field(default=False, alias="isError")
    structured_content: Any = Field(default=None, alias="structuredContent")

    @property
    def text(self) -> str:
        """The text parts of ``content`` joined with newlines."""
        return "\n".join(
            str(part.get("text", "")) for part in self.content if part.get("type") == "text"
        )

    def data(self) -> Any:
        """``structured_content`` when present, else the text parsed as JSON."""
        if self.structured_content is not None:
            return self.structured_content
        try:
            return json.loads(self.text)
        except ValueError as error:
            raise ForwardResponseError(
                "the tool's text is not JSON", payload=self.text[:2000], model_name="tool result"
            ) from error
