from __future__ import annotations

from base_classes import InteractionAction
from mcp.client import get_or_create_client, inject_demo_server


class McpDemoAction(InteractionAction):
    """Load a demo MCP server named 'testmcp' with a couple of tools.

    Usage: load mcp demo
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        client = get_or_create_client(self.session)
        conn = inject_demo_server(client, 'testmcp')
        # Also prepare provider pass-through for built-in tool path
        try:
            params = self.session.get_params() or {}
            ebt = params.get('enable_builtin_tools')
            names = []
            if isinstance(ebt, str) and ebt.strip():
                names = [s.strip() for s in ebt.split(',') if s.strip()]
            if 'mcp' not in names:
                names.append('mcp')
            self.session.config.set_option('enable_builtin_tools', ','.join(names))
            servers = params.get('mcp_servers')
            mapping = {}
            if isinstance(servers, dict):
                mapping.update({str(k): str(v) for k, v in servers.items()})
            elif isinstance(servers, str) and servers.strip():
                for item in servers.split(','):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        mapping[k.strip()] = v.strip()
            mapping['testmcp'] = conn.url
            csv_val = ', '.join(f"{k}={v}" for k, v in mapping.items())
            self.session.config.set_option('mcp_servers', csv_val)
        except Exception:
            pass

        msg = "Loaded demo MCP server 'testmcp' with tools: calc.sum, echo.say"
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit('status', {'message': msg})
            else:
                self.session.utils.output.info(msg)
        except Exception:
            print(msg)

