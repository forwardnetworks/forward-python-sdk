# Forward's MCP server

Forward serves its read-only AI tools over the Model Context Protocol at
`<url>/api/mcp`, for agents such as GitHub Copilot, Cursor or Claude Code.
`client.mcp` covers both sides: the config an MCP client needs, and a small
client of its own.

## Configuring an MCP client

`client.mcp.config()` builds the entry from the same settings the client uses,
so the Basic `Authorization` header is never assembled by hand:

```python
import json
from pathlib import Path

from forward_sdk import ForwardClient

client = ForwardClient.from_env()
Path(".vscode/mcp.json").write_text(json.dumps(client.mcp.config(style="vscode"), indent=2))
```

| `style` | File | Shape |
| --- | --- | --- |
| `"vscode"` | `.vscode/mcp.json` | `{"servers": {name: {"type": "http", "url", "headers"}}}` |
| `"cursor"` | `mcp.json` | `{"mcpServers": {name: {"url", "headers"}}}` |
| `"claude"` | Claude Code `.mcp.json` | `{"mcpServers": {name: {"type": "http", "url", "headers"}}}` |
| `"generic"` | | the server entry alone: `{"type": "http", "url", "headers"}` |

The entry is keyed `forward` unless you pass `name=`. It holds the credential, so
keep the file out of version control. `config()` raises `ForwardConfigurationError`
for a client built without a username and password.
`forward_sdk.mcp.client_config(url, username, password, name=..., style=...)` does
the same without a client.

Every Forward tool needs a network id, which the MCP config cannot carry: tell
the agent which network to use in its instructions.

## Calling tools from Python

```python
for tool in client.mcp.tools():
    print(tool.name, tool.arguments)

result = client.mcp.call("get_paths", srcIp="10.0.0.1", dstIp="10.0.1.1")
if result.is_error:
    print(result.text)
else:
    print(result.data())
client.mcp.close()
```

`call()` sends the client's network unless `network_id=` is given, and the
client's pinned snapshot (if any) unless `snapshot_id=` is; with neither, a
tool uses the network's latest processed snapshot. Check a
tool's argument names with `tools()`: `nqe`, for one, takes `prompt`, not
`query`. A tool that ran and failed returns `is_error` with Forward's message;
a protocol-level error raises `ForwardMcpError` (so does a server that opens no
session, a response that does not answer the request, or a repeated `tools/list`
cursor; `tools()` follows `nextCursor` pages), and a body that is not the expected
shape raises `ForwardResponseError`. `result.data()` returns `structured_content`
when present, otherwise the text parsed as JSON (`ForwardResponseError` if it is not
JSON); `result.text` is the text parts joined with newlines. Calls go through the client's
transport, so errors and `hooks=` apply as everywhere else. They are retried
only when Forward never saw them (a connect error, a 429): a repeated
`tools/call` would run the tool again, and some tools call a language model.

One `client.mcp` is not for concurrent use from several tasks or threads:
concurrent first calls would each open a session. Closing the client ends the
session. `close()` ignores a 404 (the session is already gone), and the next call
opens a new session. There is an async counterpart (`AsyncMcpService`) with the same
methods awaited.

## What the server actually does

- **It is session-based**, although Forward's documentation calls it
  stateless. A call without a session gets 400 `Session ID required in
  mcp-session-id header`. `client.mcp` opens a session with `initialize` on
  first use, reuses it, and opens a new one once if Forward forgets it (404).
  `close()` ends it.
- **Answers are Server-Sent Events**, even for a single call.
- **The tool list depends on the account.** One account saw 11 tools; the
  AutoCon6 workshop's network-scoped seat key saw 14, the extra three being
  `nqe`, `search_docs` and `generate_nqe_query`.
- The server is on when the org property `ENABLE_MCP_SERVER` is (the default)
  and AI is available to the organization.

Seen live on 26.10.6 (2026-11-15): sessions, SSE answers, the tool list,
calls, a tool error, reopening after a lost session, and `close()` (DELETE
answers 200, and the old session id then answers 404). The rest is from
Forward's source.

## Source

- `src/forward_sdk/mcp.py`
- `src/forward_sdk/_async/services/mcp.py`
- `src/forward_sdk/_sync/services/mcp.py`
- `src/forward_sdk/_sync/client.py`
