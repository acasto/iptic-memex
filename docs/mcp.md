# MCP (Model Context Protocol)

MCP support includes provider pass-through (OpenAI Responses, Anthropic) and app-side clients (`http` and `stdio`).

Global gate: `[MCP].active = true|false` acts as an on/off switch for all MCP features.

Global defaults (config.ini -> `[MCP]`):
- `mcp_servers = name1,name2` (optional; if omitted, all defined `[MCP.<name>]` are considered)
- `autoload = name1,name2` (connect these servers at startup)
- `auto_alias = true|false` (add short aliases when no conflicts exist)

Per-server (config.ini -> `[MCP.<name>]`):
- `transport = provider|http|stdio`
- `url` or `command` (depending on transport), `headers` (JSON/dict; supports `${env:VAR}`)
- `allowed_tools` (CSV) - limits provider pass-through and filters app-side registration
- `require_approval = never|always` - provider pass-through hint when supported
- Overrides: `autoload`, `auto_alias` (inherit global when omitted)

CLI helpers:
- `mcp status`, `mcp provider` - inspect support and configuration
- `mcp tools`, `mcp resources` - list app-side discoveries
- `mcp load <server>`; `mcp unload <server>`
- `discover mcp tools <server>` - probe tools
- `register mcp tools <server> [--tools t1,t2] [--alias]` - expose to the assistant
- `show tools` - assistant-visible tool names (deduped), annotated with server

Tip: For MCP diagnostics, enable `[LOG].log_mcp = detail` (and optionally `mirror_to_console = true`).

For app-side implementation details, see `memex_mcp/README.md`.
