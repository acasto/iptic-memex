from base_classes import InteractionAction
from core.input_limits import enforce_interactive_gate
from contextlib import nullcontext


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

        scope_cm = nullcontext()
        scope_applied = False
        if auto_submit:
            try:
                scope_meta = self.session.get_user_data('__last_tool_scope__')
            except Exception:
                scope_meta = None
            scope_callable = getattr(output, 'tool_scope', None)
            tool_name = (scope_meta or {}).get('tool_name') if scope_meta else None
            if callable(scope_callable):
                scope_cm = scope_callable(
                    tool_name or 'contexts',
                    call_id=(scope_meta or {}).get('tool_call_id') if scope_meta else None,
                    title=(scope_meta or {}).get('title') if scope_meta else None,
                )
                scope_applied = True

        with scope_cm:
            if contexts:
                total_tokens = 0
                printed_any = False

                params = self.session.get_params()
                show_context_summary = params.get('show_context_summary', True)
                show_context_details = params.get('show_context_details', False)
                detail_max_chars = params.get('context_detail_max_chars', 4000)

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

                    if show_context_details and context['type'] in ('assistant', 'agent') and content:
                        try:
                            text_val = str(content)
                        except Exception:
                            text_val = ''
                        if text_val:
                            if isinstance(detail_max_chars, int) and detail_max_chars > 0 and len(text_val) > detail_max_chars:
                                output.write(text_val[:detail_max_chars])
                                output.write(f"\n… (truncated {len(text_val) - detail_max_chars} chars)\n")
                            else:
                                output.write(text_val)
                            printed_any = True

                if auto_submit and total_tokens > 0:
                    try:
                        in_agent = bool(getattr(self.session, 'in_agent_mode', lambda: False)())
                    except Exception:
                        in_agent = False
                    if not in_agent:
                        decision = enforce_interactive_gate(self.session, total_tokens)
                        action = decision.get('action')
                        lim = decision.get('limit')
                        if action == 'disable_auto':
                            output.write(f"\nWarning: Total tokens ({total_tokens}) exceed limit ({lim}). Auto-submit disabled.")
                            self.session.set_flag('auto_submit', False)
                        elif action == 'feedback':
                            self.session.add_context('assistant', {
                                'name': 'assistant_feedback',
                                'content': f"Warning: Input size ({total_tokens} tokens) exceeds recommended limit ({lim})."
                            })

                if printed_any and (not auto_submit):
                    output.write()

        if scope_applied:
            try:
                self.session.set_user_data('__last_tool_scope__', None)
            except Exception:
                pass

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
