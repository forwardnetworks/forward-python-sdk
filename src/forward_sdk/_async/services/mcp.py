"""Forward's MCP server, from Python: the client config and a thin client."""

from __future__ import annotations

import builtins
import contextlib
import itertools
from typing import Any

from forward_sdk._async.services._base import AsyncService
from forward_sdk._ops import mcp as ops
from forward_sdk._version import __version__
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardMcpError,
    ForwardNotFoundError,
    ForwardResponseError,
)
from forward_sdk.mcp import (
    PROTOCOL_VERSION,
    McpStyle,
    McpTool,
    McpToolResult,
    client_config,
    parse_messages,
)

__all__ = ["AsyncMcpService"]


class AsyncMcpService:
    """Forward's MCP server (``/api/mcp``): read-only AI tools over the Model Context Protocol.

    :meth:`config` writes an MCP client's entry from this client's settings.
    :meth:`tools` and :meth:`call` talk to the server with the client's
    retries, errors and hooks. The server is session-based (Forward's docs say
    otherwise): the first call opens a session, which is reused and reopened
    once if Forward forgets it. :meth:`close` ends it, and closing the client
    does too. Not for concurrent use from several tasks or threads at once:
    concurrent first calls would each open a session.
    """

    def __init__(self, service: AsyncService) -> None:
        self._service = service
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._ids = itertools.count(1)
        self.server_info: dict[str, Any] = {}

    # -- configuration ---------------------------------------------------

    def config(self, *, name: str = "forward", style: McpStyle = "generic") -> dict[str, Any]:
        """An MCP client entry for this client's Forward, with its credentials.

        ``style`` is ``"vscode"``, ``"cursor"``, ``"claude"`` or ``"generic"``;
        see :func:`forward_sdk.mcp.client_config`. The result holds the
        credential: keep the file it is written to out of version control.
        """
        config = self._service._config
        if config.username is None or config.password is None:
            raise ForwardConfigurationError(
                "the MCP config needs this client's username and password"
            )
        return client_config(
            config.base_url, config.username, config.password, name=name, style=style
        )

    # -- the protocol ----------------------------------------------------

    async def _post(self, body: dict[str, Any]) -> builtins.list[Any]:
        response = await self._service._transport.send(
            ops.message(
                body=body, session_id=self._session_id, protocol_version=self._protocol_version
            )
        )
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        return parse_messages(response.text, response.headers.get("content-type"))

    async def _open(self) -> None:
        self._session_id = None
        self._protocol_version = None
        try:
            result = await self._exchange(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "forward-sdk", "version": __version__},
                },
                opening=True,
            )
            self._protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
            self.server_info = dict(result.get("serverInfo") or {})
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            # A session that did not finish opening is not one: the next call opens another.
            self._session_id = None
            self._protocol_version = None
            raise

    async def _exchange(
        self, method: str, params: dict[str, Any], *, opening: bool = False
    ) -> dict[str, Any]:
        message_id = next(self._ids)
        body = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        messages = await self._post(body)
        for message in messages:
            # Only a response answers: a server request may reuse the number.
            if (
                not isinstance(message, dict)
                or message.get("id") != message_id
                or not ("result" in message or "error" in message)
            ):
                continue
            error = message.get("error")
            if error:
                detail = error if isinstance(error, dict) else {"message": error}
                raise ForwardMcpError(
                    f"{method}: {detail.get('message', error)}",
                    code=detail.get("code"),
                    data=detail.get("data"),
                )
            if opening and not self._session_id:
                raise ForwardMcpError("Forward's MCP server opened no session (no mcp-session-id)")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ForwardResponseError(
                    f"{method}: the result is not a JSON object", payload=result
                )
            return dict(result)
        raise ForwardMcpError(f"{method}: no answer to request {message_id}")

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._session_id is None:
            await self._open()
        try:
            return await self._exchange(method, params)
        except ForwardNotFoundError:
            # The MCP transport answers 404 for a session it no longer knows.
            await self._open()
            return await self._exchange(method, params)

    # -- tools -----------------------------------------------------------

    async def tools(self) -> builtins.list[McpTool]:
        """The tools the server offers (``tools/list``), with their argument schemas."""
        tools: builtins.list[McpTool] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(McpTool.model_validate(tool) for tool in result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                raise ForwardMcpError(f"tools/list: the server repeated the cursor {cursor!r}")
            seen.add(cursor)

    async def call(
        self,
        name: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        **arguments: Any,
    ) -> McpToolResult:
        """Run one tool (``tools/call``).

        Every Forward tool takes a network: ``network_id`` defaults to the
        client's, and ``snapshot_id`` to the client's pinned snapshot like every
        other call. With neither, the tool uses the network's latest processed
        snapshot (Forward source). Other arguments go as given; check
        a tool's names with :meth:`tools` (``nqe`` takes ``prompt``, not
        ``query``). A tool that ran and failed returns ``is_error`` set; a
        protocol error raises :class:`~forward_sdk.errors.ForwardMcpError`.
        """
        args = {"networkId": self._service._network(network_id), **arguments}
        snapshot = self._service._snapshot(snapshot_id)
        if snapshot is not None:
            args["snapshotId"] = snapshot
        result = await self._request("tools/call", {"name": name, "arguments": args})
        return McpToolResult.model_validate(result)

    async def close(self) -> None:
        """End the session, if one is open. A later call opens a new one."""
        session, self._session_id = self._session_id, None
        if session is None:
            return
        with contextlib.suppress(ForwardNotFoundError):  # already gone
            await self._service._transport.send(
                ops.end_session(session_id=session, protocol_version=self._protocol_version)
            )
