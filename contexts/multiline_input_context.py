from base_classes import InteractionContext


class MultilineInputContext(InteractionContext):
    """
    Class for handling code snippets
    """

    def __init__(self, session, context_data=None):
        self.session = session
        if context_data is None:
            context_data = {}
        if 'name' not in context_data or context_data['name'] == '':
            context_data['name'] = 'Multiline Input'
        if 'content' not in context_data or context_data['content'] == '':
            context_data['content'] = 'No content provided'

        self.multiline_input = {'name': context_data['name'], 'content': context_data['content']}

    def get(self):
        return self.multiline_input
