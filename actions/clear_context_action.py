from session_handler import InteractionAction


class ClearContextAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session
        self.contexts = session.get_action('process_contexts')
        self.token_counter = self.session.get_action('count_tokens')
        self.contexts = self.contexts.get_contexts(self.session)  # fetch a list of contexts excluding chat and prompt

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

        if len(self.contexts) == 0:
            print(f"No contexts to clear.\n")
            return True

        # if args is a string "all" or a list with "all" in the first position, clear all contexts
        if isinstance(args, str) and args == 'all' or len(args) > 0 and args[0] == 'all':
            self.clear_all_contexts()
            return True

        # in the args list are strings that can be a digit or a word, if a digit we need to convert to int
        args = [int(arg) if arg.isdigit() else arg for arg in args]

        # If no context index is given, list the contexts and ask the user for input
        if len(args) == 0:
            if len(self.contexts) > 0:
                for idx, context in enumerate(self.contexts):
                    tokens = self.token_counter.count_tiktoken(context['context'].get()['content'])
                    print(f"In context: [{idx}] {context['context'].get()['name']} ({tokens} tokens)")
                print()
            user_input = input("Enter the index of the context to remove: ").strip()
            if user_input:  # Check if input is not empty
                item = int(user_input)
            else:
                print("No context index provided. Returning.")
                return True
        else:
            item = args[0]

        # Remove the given context
        try:
            self.session.remove_context_item(self.contexts[item]['type'], self.contexts[item]['idx'])
        except TypeError:
            print("Invalid context index.")

        print()
        return True

    def clear_all_contexts(self):
        """
        Clear all contexts from the session
        """
        # We reverse the indices to remove the last context first, so the indices don't change
        context_indices = sorted([(context['type'], context['idx']) for context in self.contexts], key=lambda x: x[1], reverse=True)

        for context_type, context_idx in context_indices:
            self.session.remove_context_item(context_type, context_idx)

        print("All contexts cleared.\n")
