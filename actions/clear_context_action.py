from session_handler import InteractionAction


class ClearContextAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session
        self.contexts = session.get_action('process_contexts')
        self.token_counter = self.session.get_action('count_tokens')

    def run(self, args=None):
        """
        Remove the context from the session
        """
        # Get the contexts in the form of a list of dictionaries with the following keys
        # 'type' - the type of context
        # 'idx' - the original index of the context in the list of contexts (to be able to reference it later)
        # 'context' - the context object
        if args is None:
            args = []
        contexts = self.contexts.get_contexts(self.session)

        if len(contexts) == 0:
            print(f"No contexts to clear.\n")
            return True

        # in the args list are strings that can be a digit or a word, if a digit we need to convert to int
        args = [int(arg) if arg.isdigit() else arg for arg in args]

        # If no context index is given, list the contexts and ask the user for input
        if len(args) == 0:
            if len(contexts) > 0:
                for idx, context in enumerate(contexts):
                    tokens = self.token_counter.count_tiktoken(context['context'].get()['content'])
                    print(f"In context: [{idx}] {context['context'].get()['name']} ({tokens} tokens)")
                print()
            item = int(input("Enter the index of the context to remove: ").strip())
        else:
            item = args[0]

        # Remove the given context
        try:
            self.session.remove_context_item(contexts[item]['type'], contexts[item]['idx'])
        except TypeError:
            print("Invalid context index.")

        print()
        return True
