from base_classes import InteractionContext


class RagContext(InteractionContext):
    """
    Minimal context for RAG results: holds name and content string.
    """

    def __init__(self, session, context_data=None):
        self.session = session
        data = context_data or {}
        name = data.get('name') or 'RAG Results'
        content = data.get('content') or ''
        self.payload = {'name': name, 'content': content}

    def get(self):
        return self.payload

