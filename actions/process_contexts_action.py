from base_classes import InteractionAction


class ProcessContextsAction(InteractionAction):
    """
    Class for processing contexts
    """
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
                    context_data = context['context'].get()
                    content = context_data.get('content', '')
                    if content:
                        tokens = self.token_counter.count_tiktoken(content)
                        total_tokens += tokens
                    else:
                        tokens = 0

                    if auto_submit:
                        output.write(f"Output of: {context_data.get('name', 'unnamed')} ({tokens} tokens)")
                    else:
                        output.write(f"In context: [{idx}] {context_data.get('name', 'unnamed')} ({tokens} tokens)")

            # Check total tokens against large_input_limit if auto_submit is True
            if auto_submit and total_tokens > 0:
                limit = int(self.session.get_tools().get('large_input_limit', 4000))
                if total_tokens > limit:
                    if self.session.get_tools().get('confirm_large_input', True):
                        output.write(f"\nWarning: Total tokens ({total_tokens}) exceed limit ({limit}). Auto-submit disabled.")
                        self.session.set_flag('auto_submit', False)
                    else:
                        self.session.add_context('assistant', {
                            'name': 'assistant_feedback',
                            'content': f"Warning: Input size ({total_tokens} tokens) exceeds recommended limit ({limit})."
                        })

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
                if 'content' in file:
                    turn_context += f"<|results:{file['name']}|>\n{file['content']}\n<|end_file:{file['name']}|>\n"
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
        for context_type in session.context:  # get the dict of context type lists directly
            if context_type != 'prompt' and context_type != 'chat':  # Ignore the prompt and chat contexts
                for idx, context in enumerate(session.context[context_type]):
                    contexts.append({'type': context_type, 'idx': idx, 'context': context})
        return contexts