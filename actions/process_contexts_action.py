from session_handler import InteractionAction


class ProcessContextsAction(InteractionAction):
    """
    Class for processing contexts
    """
    # Todo: Some of this functionality might be better suited for the session handler. Maybe consider keeping
    #       the user interaction stuff that displays context and move the get_contexts and assistant stuff elsewhere

    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')

    def run(self, args=None):
        """
        Process the contexts that have been loaded into the session
        """
        if args == "user" or args is None:
            return self.process_contexts_for_user()

    def process_contexts_for_user(self, auto_submit=False) -> list:
        """
        Takes in a list of contexts, prints them out for the user, and returns the list back for processing to chat
        params: contexts (list) - list of context dictionaries
        return: contexts (list) - list of context dictionaries
        """
        output = self.session.utils.output
        contexts = self.session.get_action("process_contexts").get_contexts(self.session)

        if len(contexts) > 0:
            output.write()
            total_tokens = 0

            for idx, context in enumerate(contexts):
                if context['type'] == 'image':
                    if auto_submit:
                        output.write(f"Output of: {context['context'].get()['name']} (image)")
                    else:
                        output.write(f"In context: [{idx}] {context['context'].get()['name']} (image)")
                else:
                    tokens = self.token_counter.count_tiktoken(context['context'].get()['content'])
                    total_tokens += tokens
                    if auto_submit:
                        output.write(f"Output of: {context['context'].get()['name']} ({tokens} tokens)")
                    else:
                        output.write(f"In context: [{idx}] {context['context'].get()['name']} ({tokens} tokens)")

            # Check total tokens against max_input if auto_submit is True
            if auto_submit and total_tokens > 0:
                max_input = self.session.conf.get_option('TOOLS', 'max_input', fallback=4000)
                if total_tokens > max_input:
                    output.write(f"\nWarning: Total tokens ({total_tokens}) exceed maximum ({max_input}). Auto-submit disabled.")
                    self.session.set_flag('auto_submit', False)

            output.write()

        return contexts

    @staticmethod
    def process_contexts_for_assistant(contexts: list) -> str:
        turn_context = ""
        is_project = False

        # go through each object and place the contents in tags
        for f in contexts:
            if f['type'] == 'raw':
                file = f['context'].get()
                turn_context += file['content']
                continue
            elif f['type'] == 'image':
                # Images get handled differently by each provider
                # Just pass through the full context
                continue
            elif f['type'] != 'project':
                file = f['context'].get()
                turn_context += f"<|file:{file['name']}|>\n{file['content']}\n<|end_file:{file['name']}|>\n"
            else:
                is_project = True
                project = f['context'].get()
                turn_context += f"<|project_notes|>\nProject Name: {project['name']}\nProject Notes: {project['content']}\n<|end_project_notes|>\n"

        if is_project:
            turn_context = "<|project_context>" + turn_context + "<|end_project_context|>"

        return turn_context

    @staticmethod
    def get_contexts(session) -> list:
        """
        Get the contexts (excluding chat and prompt) from the session in a way they can be used in other actions
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
        return contexts
