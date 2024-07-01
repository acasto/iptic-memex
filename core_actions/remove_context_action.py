from session_handler import InteractionAction


class RemoveContextAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session

    def run(self, remove_context=None):
        """
        Get the list of contexts and remove the specified context
        """
        contexts = []
        for context in self.session.get_context():
            if context != 'prompt' and context != 'chat':  # Ignore the prompt and chat contexts
                contexts.extend(self.session.get_context(context))

        # Remove the specified context
        if len(contexts) > 0:
            pass
            #  todo: we probably need to add an ID property to the context objects so we can reference them
            #        across various parts of the codebase.
