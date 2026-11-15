"""Request builders for Forward's MCP server (``/api/mcp``).

One route carries every JSON-RPC message; ``client.mcp`` handles the session.
"""

from __future__ import annotations

from typing import Any

from forward_sdk._ops import RequestSpec, op, spec_for

#: MCP's streamable-HTTP transport answers with JSON or an SSE stream.
MCP_ACCEPT = "application/json, text/event-stream"


def _headers(session_id: str | None, protocol_version: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if session_id:
        headers["mcp-session-id"] = session_id
    if protocol_version:
        headers["mcp-protocol-version"] = protocol_version
    return headers


@op("mcpRequest")
def message(
    *,
    body: dict[str, Any],
    session_id: str | None = None,
    protocol_version: str | None = None,
) -> RequestSpec:
    """Send one JSON-RPC message.

    Left non-idempotent on purpose: a repeated ``initialize`` would leave a
    session open on the server, and a repeated ``tools/call`` re-runs the tool
    (some call a language model) and records its use twice. So only a failure
    Forward never saw (a connect error, a 429) is retried.
    """
    return spec_for(
        "mcpRequest",
        json=body,
        accept=MCP_ACCEPT,
        headers=_headers(session_id, protocol_version),
    )


@op("mcpEndSession")
def end_session(*, session_id: str, protocol_version: str | None = None) -> RequestSpec:
    return spec_for(
        "mcpEndSession", accept=MCP_ACCEPT, headers=_headers(session_id, protocol_version)
    )
