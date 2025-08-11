from base_classes import InteractionContext


class AgentContext(InteractionContext):
    """
    Lightweight context for agent loop hints or status messages that should be
    included in the assistant's next prompt via the normal context pipeline.
    """

    def __init__(self, session, context_data=None):
        self.session = session
        context_data = context_data or {}
        name = context_data.get('name') or 'agent'
        content = context_data.get('content') or ''
        self._data = {'name': name, 'content': content}

    def get(self):
        return self._data

