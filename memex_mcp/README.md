memex_mcp (app-side MCP)
---------------------------------

This package contains the app-side Model Context Protocol (MCP) integration for iptic‑memex.

- Namespacing: it is intentionally named `memex_mcp` to avoid colliding with the official `mcp` Python SDK when both are installed. The SDK is imported as `mcp`.
- SDK-first: the client prefers the official MCP Python SDK (`mcp`) and supports streamable HTTP sessions. A minimal HTTP fallback is available behind a flag.

Key behaviors
- Autoload: if `[MCP].active=true` and `autoload = name1,name2`, the bootstrap connects those servers and registers discovered tools for this session. Aliases default to on (`auto_alias=true`) and can be overridden per server.
- API-safe tool names: dynamic tools are exposed to providers with API-safe names (^[A-Za-z0-9_-]+$), preferring a valid alias and otherwise sanitizing. The registry records a mapping `__tool_api_to_cmd__` so providers can map tool calls back to their canonical command keys.
- Fixed args: dynamic tool specs include `function.fixed_args` (e.g., `{server, tool}`), and the TurnRunner merges them into provider tool calls before executing the action.
- Result formatting: MCP tool outputs (SDK `CallToolResult` or dict results) are formatted into readable text for tool role messages (text/json/resource stubs). No raw object dumping.
- Resource discovery: SDK `list_resources()` is used when available. An HTTP `/resources` fallback is available behind a flag.

Config options (config.ini → [MCP])
- `active = true|false`: gate MCP features.
- `use_sdk = true|false`: prefer the Python SDK when installed.
- Diagnostics: use centralized logging. Set `[LOG].log_mcp = detail` (and optionally `mirror_to_console = true`) to emit one-line diagnostics for MCP operations.
- `http_fallback = true|false`: enable generic HTTP JSON endpoints (/tools,/call,/resource,/resources).
- `mcp_servers = name1,name2` (optional): declare known server subsections `[MCP.<name>]`. If omitted, all defined `[MCP.<name>]` are considered.
- `autoload = name1,name2`: autoload these servers on session build (connect + register).
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
- `auto_alias = true|false`: per-server override of global alias creation.

Commands
- `/mcp load <server>`: connect a configured server and register its tools (honors `allowed_tools` and `auto_alias`).
- `/mcp unload <server>`: disconnect and remove dynamic tools for that server (also updates provider pass-through).
- `/mcp on|off`: enable/disable MCP for the session; `on` autoloads configured servers, `off` unloads and disables.

Notes
- The official MCP SDK surfaces client/session functions (e.g., `ClientSession`, `streamablehttp_client`). The client adapts to SDK layouts across versions; when logging is set to `log_mcp = detail`, import attempts and fallback paths are recorded via the central logger.
- Dynamic tools are always included in the tool registry (not filtered by `[TOOLS].active_tools`).
