from session_handler import InteractionContext


class ProjectContext(InteractionContext):
    """
    Class for handling code snippets
    """

    def __init__(self, session, context_data=None):
        self.session = session
        if context_data is None:
            context_data = {}
        if 'name' not in context_data or context_data['name'] == '':
            context_data['name'] = None
        if 'content' not in context_data or context_data['content'] == '':
            context_data['content'] = None

        self.project = {'name': context_data['name'], 'content': context_data['content']}

    def get(self):
        return self.project
