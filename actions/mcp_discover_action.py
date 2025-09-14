from __future__ import annotations

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client


class McpDiscoverAction(InteractionAction):
    """Discover tools from a connected MCP server and show them.

    Usage:
      - discover mcp tools <server>
      - discover mcp tools            (lists all servers + tools)
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        args = args or []
        server = None
        if isinstance(args, (list, tuple)) and args:
            server = str(args[0])

        client = get_or_create_client(self.session)
        data = client.list_tools(server)
        lines = ["MCP tool discovery:"]
        for name, tools in data.items():
            lines.append(f"- {name}:")
            if not tools:
                lines.append("    (no tools discovered)")
                continue
            for t in tools:
                try:
                    props = list((t.input_schema or {}).get('properties', {}).keys())
                except Exception:
                    props = []
                lines.append(f"    • {t.name}  ({', '.join(props) if props else 'no schema'})")

        msg = "\n".join(lines)
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit('status', {'message': msg})
            else:
                self.session.utils.output.write(msg)
        except Exception:
            try:
                self.session.utils.output.info(msg)
            except Exception:
                pass
