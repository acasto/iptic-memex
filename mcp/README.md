# MCP Integration Overview (iptic-memex)

Status: opt-in; production-ready provider pass-through; app-side client with SDK/HTTP support.

Last updated: 2025-09-06

## What This Adds
- Two ways to use MCP (Model Context Protocol) tools in this app:
  1) Provider pass-through (OpenAI Responses and Anthropic): let the model provider connect to remote MCP servers.
  2) App-side client (portable): discover/register remote MCP tools and invoke them through our normal tool loop across any provider.

## Key Components
- `mcp/client.py` (session-scoped client facade)
  - SDK transport (optional, preferred when installed): uses the official MCP Python SDK (module `mcp` or `modelcontextprotocol`).
  - HTTP fallback: simple JSON endpoints for `/tools`, `/call`, `/resource` when SDK is unavailable.
  - Demo injection: `inject_demo_server()` creates a local demo server named `testmcp` with two tools.
- `actions/`
  - `mcp_connect_action.py`: `load mcp [http|stdio] <name> <url|cmd>` — add a server to the session.
  - `mcp_list_action.py`: `list mcp [tools|resources]` — list servers/tools/resources.
  - `mcp_discover_action.py`: `discover mcp tools [<server>]` — list server tools + schema properties.
  - `mcp_register_tools_action.py`: `register mcp tools <server> [--tools a,b] [--alias]` — register dynamic tools.
  - `mcp_unregister_tools_action.py`: `unregister mcp tools [pattern]` — remove dynamic tools.
  - `mcp_proxy_tool_action.py`: generic executor invoked by dynamic tool specs; forwards to client.
  - `mcp_fetch_resource_action.py`: `load mcp resource <server> <uri>` — fetches resource content into context.
  - `mcp_demo_action.py`: `load mcp demo` — add the demo server `testmcp` with sample tools/resources.
  - `mcp_doctor_action.py`: `mcp doctor` — diagnostics (SDK detection, servers, transports).
- `contexts/mcp_resources_context.py`: holds fetched resource content as a normal context block.
- `providers/`
  - `openairesponses_provider.py` & `anthropic_provider.py`: optional pass-through wiring for built-in MCP tool(s)
    via `enable_builtin_tools = mcp` and `mcp_servers = label=url` (see config below).
- `actions/assistant_commands_action.py`: merges session dynamic tool specs and supports `function.fixed_args`.

## Quick Start (Demo)
1) Enable MCP feature: set `[MCP].active = True` in your user config.
2) Load the demo server: `load mcp demo`
3) Inspect: `mcp doctor`, `list mcp tools`, `discover mcp tools testmcp`
4) Register tools: `register mcp tools testmcp --alias`
   - Tools appear as `mcp:testmcp/calc.sum` and `mcp:testmcp/echo.say` (aliases without the prefix are added when `--alias` is used and no conflicts exist).
5) Use them in chat — models can call these tools like any other.
6) Fetch a resource: `load mcp resource testmcp guides/welcome`

## Provider Pass-Through (OpenAI / Anthropic)
Enable in config to let providers handle MCP directly.

```ini
[OpenAIResponses]
enable_builtin_tools = mcp
mcp_servers = mysvc=https://your-mcp.server
; Optional per-server overrides
; mcp_headers_mysvc = {"Authorization": "Bearer ..."}
; mcp_allowed_mysvc = tool1,tool2
; mcp_require_approval = never  ; or always (global or per label with _<label>)

[Anthropic]
enable_builtin_tools = mcp
mcp_servers = mysvc=https://your-mcp.server
; same optional keys as above (headers/allowed/require_approval)
```

Notes
- Simplest way to use real servers today; runs alongside local tools.
- Works only on providers that support MCP built-ins. Our app sends configuration; the provider does the rest.

## App-Side Client (Portable Path)
Configuration (config.ini):

```ini
[MCP]
active = False        ; gate — nothing loads until true
use_sdk = True        ; prefer official SDK when installed (falls back to HTTP JSON)
; default_transport = http
; connect_timeout = 10s
; allow_dynamic_tools = True
```

Runtime commands:
- `load mcp http <name> <url>` — add server by URL.
- `load mcp stdio <name> <cmd>` — add server via stdio (requires SDK transport support).
- `discover mcp tools <name>` — see tools & schema.
- `register mcp tools <name> [--tools a,b] [--alias]` — register as first-class tools.
- `unregister mcp tools [pattern]` — remove dynamic tools.
- `load mcp resource <name> <uri>` — fetch content into the `mcp_resources` context.
- `mcp doctor` — confirm SDK detection, transports, and connected servers.

## Dynamic Tool Registration Details
- Registered tool names are namespaced to avoid collisions: `mcp:<server>/<tool>`.
- Each spec is pinned to the server/tool via `function.fixed_args` so models only supply the schema-defined inputs.
- Optional `--alias` adds a pretty alias (e.g., `calc.sum`) when no conflict exists; local tools always win on plain names.
- Session scope: dynamic specs live in `session.user_data['__dynamic_tools__']` and reset when a new session starts.

## HTTP JSON Fallback (for simple servers or testing)
When the SDK is absent or disabled, the client uses:
- GET  `<server_url>/tools`  → `[ {name, description, inputSchema}, ... ]`
- POST `<server_url>/call`   → body `{tool, arguments}`; returns `{content:[...]}`
- GET  `<server_url>/resource?uri=...` → `{name, content, metadata}`

This is a pragmatic baseline and not the full MCP spec; the official SDK path should be used for spec-compliant servers.

## Security & Policy
- Opt-in feature flag: `[MCP].active` must be true.
- Provenance: outputs and resources are tagged (e.g., `source="mcp:<server>/<tool>"`).
- Existing gates apply (e.g., `[TOOLS].large_input_limit`, `confirm_large_input`).
- Provider pass-through supports allowlists and approval settings. For app-side tools, use filters with `register mcp tools` or build a server-side allow list.
- Network hygiene: the app-side HTTP client respects headers/timeouts; prefer SDK transport for stdio and full spec semantics.

## Dev/Test Notes
- Demo: `load mcp demo` adds `testmcp` with tools `calc.sum`, `echo.say`, and resource `guides/welcome`.
- Tests: see `tests/mcp/` for dynamic registration, proxy behavior, and resource fetch. Entire suite: 95 tests passing.

## Troubleshooting
- `mcp doctor` shows whether the SDK is detected (`sdk_available`) and which transport will be used.
- If using provider pass-through, verify `enable_builtin_tools = mcp` and `mcp_servers = ...` are present under the provider matching the active model.
- If tool names collide, use the namespaced form `mcp:<server>/<tool>` explicitly.

