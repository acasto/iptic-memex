from __future__ import annotations

from base_classes import InteractionContext


class McpResourcesContext(InteractionContext):
    """Holds content loaded from MCP resources with provenance metadata.

    This mirrors FileContext shape: {'name', 'content', 'metadata'} so it
    flows through the normal context processing pipeline.
    """

    def __init__(self, session, item=None):
        self.session = session
        self.item = {}
        if isinstance(item, dict):
            name = item.get('name') or item.get('uri') or 'MCP Resource'
            content = item.get('content') or ''
            meta = dict(item.get('metadata') or {})
            meta.setdefault('source', item.get('source') or 'mcp')
            meta.setdefault('uri', item.get('uri') or '')
            self.item = {'name': name, 'content': content, 'metadata': meta}
        elif isinstance(item, str):
            # Minimal: treat string as a URI placeholder
            self.item = {'name': item, 'content': '', 'metadata': {'source': 'mcp', 'uri': item}}

    def get(self):
        return self.item

