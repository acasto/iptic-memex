from __future__ import annotations

from base_classes import InteractionAction
from mcp.client import get_or_create_client


class McpListAction(InteractionAction):
    """List connected MCP servers, tools, or resources.

    Usage (user commands):
      - list mcp tools
      - list mcp resources
      - list mcp
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        what = 'servers'
        if isinstance(args, (list, tuple)) and args:
            token = str(args[0]).lower()
            if token in ('tools', 'resources', 'servers'):
                what = token

        client = get_or_create_client(self.session)
        if what == 'tools':
            data = client.list_tools()
            self._print_kv_list("MCP tools", {k: [t.name for t in v] for k, v in data.items()})
        elif what == 'resources':
            data = client.list_resources()
            self._print_kv_list("MCP resources", {k: [r.uri for r in v] for k, v in data.items()})
        else:
            servers = client.list_servers()
            info = {s.name: f"{s.transport}:{s.url or s.cmd}" for s in servers}
            self._print_kv_list("MCP servers", info)

    # --- helpers ------------------------------------------------------------
    def _print_kv_list(self, title: str, data: dict):
        try:
            blocking = bool(self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False))
        except Exception:
            blocking = True

        lines = [title + ":"]
        if not data:
            lines.append("  (none)")
        else:
            for k, v in data.items():
                if isinstance(v, (list, tuple)):
                    v_str = ", ".join(v) if v else "(none)"
                else:
                    v_str = str(v)
                lines.append(f"  - {k}: {v_str}")

        msg = "\n".join(lines)
        if not blocking and self.session.ui:
            self.session.ui.emit('status', {'message': msg})
        else:
            self.session.utils.output.write(msg)

