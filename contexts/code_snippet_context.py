from base_classes import InteractionContext


class CodeSnippetContext(InteractionContext):
    """
    Class for handling code snippets
    """

    def __init__(self, session, snippet=None):
        self.session = session
        self.code_snippet = {'name': 'Code Snippet', 'content': snippet}

    def get(self):
        return self.code_snippet
