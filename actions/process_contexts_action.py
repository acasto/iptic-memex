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
            total_tokens = 0
            printed_any = False  # Track if we emitted any summary/details output

            # Toggle per-context summary printing via config/params (DEFAULT.show_context_summary)
            params = self.session.get_params()
            show_context_summary = params.get('show_context_summary', True)

            # Agent-friendly: optionally print context contents (e.g., diffs) for assistant/agent contexts
            show_context_details = params.get('show_context_details', False)
            detail_max_chars = params.get('context_detail_max_chars', 4000)

            # For interactive chat, add a spacer before summaries/details
            if (not auto_submit) and (show_context_summary or show_context_details):
                output.write()

            for idx, context in enumerate(contexts):
                if context['type'] == 'image':
                    if show_context_summary:
                        name = context['context'].get()['name']
                        if auto_submit:
                            output.write(f"Output of: {name} (image)")
                        else:
                            output.write(f"In context: [{idx}] {name} (image)")
                        printed_any = True
                    # No tokens to add for images in this counter
                    continue

                context_data = context['context'].get()
                content = context_data.get('content', '')
                tokens = self.token_counter.count_tiktoken(content) if content else 0
                total_tokens += tokens

                if show_context_summary:
                    if auto_submit:
                        output.write(f"Output of: {context_data.get('name', 'unnamed')} ({tokens} tokens)")
                    else:
                        output.write(f"In context: [{idx}] {context_data.get('name', 'unnamed')} ({tokens} tokens)")
                    printed_any = True

                # Print details for assistant/agent contexts (e.g., diffs, errors) when enabled
                if show_context_details and context['type'] in ('assistant', 'agent') and content:
                    try:
                        text = str(content)
                    except Exception:
                        text = ''
                    if text:
                        if isinstance(detail_max_chars, int) and detail_max_chars > 0 and len(text) > detail_max_chars:
                            output.write(text[:detail_max_chars])
                            output.write(f"\n… (truncated {len(text) - detail_max_chars} chars)\n")
                        else:
                            output.write(text)
                        printed_any = True

            # Check total tokens against large_input_limit if auto_submit is True
            if auto_submit and total_tokens > 0:
                limit = int(self.session.get_tools().get('large_input_limit', 4000))
                if total_tokens > limit:
                    # In Agent Mode, do not interrupt the loop; allow autonomous progression
                    try:
                        in_agent = bool(getattr(self.session, 'in_agent_mode', lambda: False)())
                    except Exception:
                        in_agent = False
                    if not in_agent:
                        if self.session.get_tools().get('confirm_large_input', True):
                            output.write(f"\nWarning: Total tokens ({total_tokens}) exceed limit ({limit}). Auto-submit disabled.")
                            self.session.set_flag('auto_submit', False)
                        else:
                            self.session.add_context('assistant', {
                                'name': 'assistant_feedback',
                                'content': f"Warning: Input size ({total_tokens} tokens) exceeds recommended limit ({limit})."
                            })

            # Only emit a trailing spacer in interactive mode
            if printed_any and (not auto_submit):
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
