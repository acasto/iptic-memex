memex_mcp (app-side MCP)
---------------------------------

This package contains the app-side Model Context Protocol (MCP) integration for iptic‑memex.

- Namespacing: it is intentionally named `memex_mcp` to avoid colliding with the official `mcp` Python SDK when both are installed. The SDK is imported as `mcp`.
- SDK-first: the client prefers the official MCP Python SDK (`mcp`) and supports streamable HTTP sessions. A minimal HTTP fallback is available behind a flag.

Key behaviors
- Autoload + auto-register: if `[MCP].active=true` and `autoload = name1,name2`, the bootstrap connects those servers and registers discovered tools for this session. Defaults: `auto_register=true`, `auto_alias=true`.
- API-safe tool names: dynamic tools are exposed to providers with API-safe names (^[A-Za-z0-9_-]+$), preferring a valid alias and otherwise sanitizing. The registry records a mapping `__tool_api_to_cmd__` so providers can map tool calls back to their canonical command keys.
- Fixed args: dynamic tool specs include `function.fixed_args` (e.g., `{server, tool}`), and the TurnRunner merges them into provider tool calls before executing the action.
- Result formatting: MCP tool outputs (SDK `CallToolResult` or dict results) are formatted into readable text for tool role messages (text/json/resource stubs). No raw object dumping.
- Resource discovery: SDK `list_resources()` is used when available. An HTTP `/resources` fallback is available behind a flag.

Config options (config.ini → [MCP])
- `active = true|false`: gate MCP features.
- `use_sdk = true|false`: prefer the Python SDK when installed.
- `debug = true|false`: emit one-line client diagnostics for suppressed errors.
- `http_fallback = true|false`: enable generic HTTP JSON endpoints (/tools,/call,/resource,/resources).
- `mcp_servers = name1,name2` (optional): declare known server subsections `[MCP.<name>]`. If omitted, all defined `[MCP.<name>]` are considered.
- `autoload = name1,name2`: autoload these servers on session build.
- `auto_register = true|false`: register discovered tools for the session.
- `auto_alias = true|false`: add pretty aliases when no conflicts exist.

Server definitions (config.ini → [MCP.<name>])
- `transport = provider|http|stdio`:
  - `provider`: use provider pass-through MCP when supported (and `[MCP].active=true`).
  - `http`: app-side HTTP client connects to `url`.
  - `stdio`: app-side stdio client runs `command`.
- `url` or `command`: target for the selected transport.
- `headers`: JSON or dict-like string; supports `${env:VAR}` expansion.
- `allowed_tools`, `require_approval`: optional pass-through hints for supporting providers.
- `allowed_tools`: also filters app-side auto-registration when set (only listed tool names will be registered for that server).
- `autoload = true|false`: per-server flag to autoload this server.
- `auto_register = true|false`: per-server override of global; when true, registers this server’s tools after connect (works for http and stdio).
- `auto_alias = true|false`: per-server override of global alias creation.

Notes
- The official MCP SDK surfaces client/session functions (e.g., `ClientSession`, `streamablehttp_client`). The client adapts to SDK layouts across versions and logs import attempts in debug mode.
- Dynamic tools are always included in the tool registry (not filtered by `[TOOLS].active_tools`).
