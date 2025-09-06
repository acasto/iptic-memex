from __future__ import annotations

from base_classes import InteractionAction
from utils.tool_args import get_str
from mcp.client import get_or_create_client


class McpFetchResourceAction(InteractionAction):
    """Fetch a resource from an MCP server and add it to context.

    Usage:
      - load mcp resource <server> <uri>
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        if isinstance(args, (list, tuple)) and len(args) >= 2:
            server = str(args[0])
            uri = ' '.join(str(x) for x in args[1:]).strip()
        else:
            server = None
            uri = None
        if not server:
            try:
                server = self.session.ui.ask_text("Server name:")
            except Exception:
                server = ''
        if not uri:
            try:
                uri = self.session.ui.ask_text("Resource URI:")
            except Exception:
                uri = ''
        if not server or not uri:
            self._emit('error', 'Usage: load mcp resource <server> <uri>')
            return

        client = get_or_create_client(self.session)
        try:
            item = client.fetch_resource(server, uri)
        except Exception as e:
            self._emit('error', f"MCP fetch failed: {e}")
            return

        meta = item.get('metadata') or {}
        meta.setdefault('source', f'mcp:{server}')
        meta.setdefault('uri', uri)
        self.session.add_context('mcp_resources', {
            'name': item.get('name') or uri,
            'content': item.get('content') or '',
            'metadata': meta,
        })
        self._emit('status', f"Loaded MCP resource '{uri}' from '{server}'.")

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

