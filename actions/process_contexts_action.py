from session_handler import InteractionAction


class ProcessContextsAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')

    def run(self, user_input=None) -> list:
        """
        Process the contexts that have been loaded into the session
        """
        contexts = self.get_contexts(self.session)

        # Let the user know what context(s) we are working with
        if len(contexts) > 0:
            print()
            for idx, context in enumerate(contexts):
                tokens = self.token_counter.count_tiktoken(context['context'].get()['content'])
                print(f"In context: [{idx}] {context['context'].get()['name']} ({tokens} tokens)")
            print()
        return contexts

    @staticmethod
    def get_contexts(session) -> list:
        """
        Get the contexts from the session in a way they can be used in other actions
        """
        # The resulting object with be a list of dictionaries with the following keys
        # 'type' - the type of context
        # 'idx' - the original index of the context in the list of contexts (to be able to reference it later)
        # 'context' - the context object
        contexts = []
        for context_type in session.get_context():  # get the dict of context type lists
            if context_type != 'prompt' and context_type != 'chat':  # Ignore the prompt and chat contexts
                for idx, context in enumerate(session.get_context(context_type)):
                    contexts.append({'type': context_type, 'idx': idx, 'context': context})
                # contexts.extend(session.get_context(context))  # build one big context list for chat turn
        return contexts
