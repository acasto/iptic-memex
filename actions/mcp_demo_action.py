from __future__ import annotations

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client, inject_demo_server


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

        msg = "Loaded demo MCP server 'testmcp' with tools: calc.sum, echo.say (provider pass-through disabled for demo)"
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit('status', {'message': msg})
            else:
                self.session.utils.output.info(msg)
        except Exception:
            try:
                self.session.utils.output.info(msg)
            except Exception:
                pass
