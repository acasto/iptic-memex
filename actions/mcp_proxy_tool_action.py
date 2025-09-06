from __future__ import annotations

import json
from base_classes import InteractionAction
from utils.tool_args import get_str
from mcp.client import get_or_create_client


class McpProxyToolAction(InteractionAction):
    """Generic MCP proxy tool.

    This presents a single callable tool (`mcp`) that forwards a call to a named
    MCP server + tool with JSON arguments. It is a bridging option until we add
    per-tool dynamic registration. Hidden unless `[MCP].active=true`.
    """

    def __init__(self, session):
        self.session = session

    # ---- Dynamic tool registry metadata ------------------------------------
    @classmethod
    def tool_name(cls) -> str:
        return 'mcp'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['server', 'tool', 'args', 'transport', 'url', 'cmd'],
            'description': (
                "Proxy a call to an MCP server tool. Provide 'server' and 'tool'. "
                "Pass JSON in 'args' or freeform content (JSON) as the body."
            ),
            'required': ['server', 'tool'],
            'schema': {
                'properties': {
                    'server': {"type": "string", "description": "Connected MCP server name."},
                    'tool': {"type": "string", "description": "Tool name on the server."},
                    'args': {"type": "string", "description": "JSON-encoded arguments for the tool."},
                    'transport': {"type": "string", "description": "When connecting inline: 'http' or 'stdio'."},
                    'url': {"type": "string", "description": "HTTP URL to connect (when inline)."},
                    'cmd': {"type": "string", "description": "Command to run (when inline stdio)."},
                    'content': {"type": "string", "description": "Optional JSON body (alternative to 'args')."},
                }
            },
            'auto_submit': True,
        }

    def run(self, args: dict, content: str = ""):
        # Inline connect (optional) for convenience
        transport = (get_str(args, 'transport') or '').lower()
        server = get_str(args, 'server')
        tool = get_str(args, 'tool')
        url = get_str(args, 'url')
        cmd = get_str(args, 'cmd')

        if not server or not tool:
            self._emit("error", "MCP: 'server' and 'tool' are required.")
            return

        client = get_or_create_client(self.session)

        if transport in ('http', 'stdio') and ((transport == 'http' and url) or (transport == 'stdio' and cmd)):
            if transport == 'http':
                client.connect_http(server, url)
            else:
                client.connect_stdio(server, cmd)

        # Parse arguments
        payload = get_str(args, 'args') or (content or '').strip()
        if payload:
            try:
                call_args = json.loads(payload)
                if not isinstance(call_args, dict):
                    raise ValueError('args must be a JSON object')
            except Exception as e:
                self._emit("error", f"MCP: invalid JSON in args/content: {e}")
                return
        else:
            call_args = {}

        # Attempt call (stub raises until real transport is wired)
        try:
            result = client.call_tool(server, tool, call_args)
        except NotImplementedError:
            # Return a structured placeholder so downstream still gets a tool message
            result = {
                'content': [
                    {"type": "text", "text": f"MCP stub: would call {server}:{tool} with {call_args}"}
                ]
            }

        # Add assistant context with provenance
        self.session.add_context('assistant', {
            'name': f"mcp:{server}/{tool}",
            'content': result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        })

    # --- helpers ------------------------------------------------------------
    def _emit(self, level: str, msg: str):
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit(level, {'message': msg})
            else:
                out = self.session.utils.output
                if level == 'error':
                    out.error(msg)
                elif level == 'warning':
                    out.warning(msg)
                else:
                    out.info(msg)
        except Exception:
            print(msg)

