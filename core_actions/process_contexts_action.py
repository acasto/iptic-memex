from session_handler import InteractionAction


class ProcessContextsAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')

    def run(self, user_input=None):
        """
        Process the contexts that have been loaded into the session
        """
        contexts = []
        for context in self.session.get_context():
            if context != 'prompt' and context != 'chat':  # Ignore the prompt and chat contexts
                contexts.extend(self.session.get_context(context))

        # Let the user know what context(s) we are working with
        if len(contexts) > 0:
            for idx, context in enumerate(contexts):
                tokens = self.token_counter.count_tiktoken(context.get()['content'])
                print(f"In context: [{idx}] {context.get()['name']} ({tokens} tokens)")
            print()
        return contexts
