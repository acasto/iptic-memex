from base_classes import InteractionMode
from actions.assistant_output_action import AssistantOutputAction


class ChatMode(InteractionMode):
    def __init__(self, session, builder=None):
        self.session = session
        self.builder = builder  # For model switching
        
        # Don't cache params - get them fresh each time
        self.utils = self.session.utils

        self.session.add_context('chat')
        self.chat = self.session.get_context('chat')
        self.process_contexts = self.session.get_action('process_contexts')
        self._budget_warning_shown = False

    @property
    def params(self):
        """Get fresh params each time instead of caching"""
        return self.session.get_params()

    def get_user_input(self):
        """
        Use the new InputHandler to gather possibly-multiline user input.
        If the user ends a line with a backslash or types a line that is just '\\',
        it will continue prompting until they finish.
        """
        # Here, we construct the prompt with colors/styling if desired.
        prompt = self.utils.output.style_text(
            self.params['user_label'],
            fg=self.params['user_label_color']
        ) + " "

        # Call the new get_input() with multiline=True and continuation_char='\\'.
        # This removes the need for any while loop here, since the InputHandler
        # does the heavy lifting.
        user_input = self.utils.input.get_input(
            prompt=prompt,
            multiline=True,
            continuation_char="\\",  # Or any other char you prefer
        )

        # The result is a single string of user input—
        # either one line or multiple lines joined together.
        return user_input

    def handle_assistant_response(self):
        """Handle getting and processing the assistant's response"""
        response_label = self.utils.output.style_text(
            self.params['response_label'],
            fg=self.params['response_label_color']
        )
        self.utils.output.write(f"{response_label} ", end='', flush=True)

        output_processor = None
        try:
            if self.params['stream']:
                stream = self.session.get_provider().stream_chat()
                if not stream:
                    return None
                output_processor = self.session.get_action('assistant_output')
                if output_processor:
                    response = output_processor.run(stream, spinner_message="")
                else:
                    # Fallback if assistant_output action not available
                    response = ""
                    for chunk in stream:
                        print(chunk, end='', flush=True)
                        response += chunk
                    print()
            else:
                response = self.session.get_provider().chat()
                if response is None:
                    return None
                # Apply non-streaming output filters for display parity
                filtered = AssistantOutputAction.filter_full_text(response, self.session)
                self.utils.output.write(filtered)
                self.utils.output.write('')
        except (KeyboardInterrupt, EOFError):
            self.utils.output.write('')
            return None

        self.chat.add(response, 'assistant')
        assistant_commands = self.session.get_action('assistant_commands')
        if assistant_commands:
            # Prefer sanitized output (think removed) if available to avoid accidental tool triggers
            try:
                sanitized = None
                if output_processor and hasattr(output_processor, 'get_sanitized_output'):
                    sanitized = output_processor.get_sanitized_output()
                # If not streaming (no output_processor), synthesize sanitized text for tools
                if sanitized is None and response is not None:
                    sanitized = AssistantOutputAction.filter_full_text_for_return(response, self.session)
                assistant_commands.run(sanitized if sanitized is not None else response)
            except Exception:
                assistant_commands.run(response)
        return response

    def check_budget(self):
        """Check if session budget exists and has been exceeded"""
        if self._budget_warning_shown:
            return

        session_budget = self.params.get('session_budget')
        if not session_budget:
            return

        try:
            budget = float(session_budget)
            provider = self.session.get_provider()
            if provider and hasattr(provider, 'get_cost'):
                cost = provider.get_cost()
                if cost and cost.get('total_cost', 0) > budget:
                    self.utils.output.warning(
                        f"Budget warning: Session cost (${cost['total_cost']:.4f}) exceeds budget (${budget:.4f})",
                        spacing=[0, 1]
                    )
                    self._budget_warning_shown = True
        except (ValueError, TypeError):
            pass

    def start(self):
        """Start the chat interaction loop"""
        self.utils.tab_completion.run('chat')
        self.utils.tab_completion.set_session(self.session)

        while True:
            # Check budget before processing contexts
            self.check_budget()

            if self.session.get_flag('auto_submit'):
                if self.process_contexts and hasattr(self.process_contexts, 'process_contexts_for_user'):
                    contexts = self.process_contexts.process_contexts_for_user(auto_submit=True)
                else:
                    contexts = []
            else:
                if self.process_contexts and hasattr(self.process_contexts, 'process_contexts_for_user'):
                    contexts = self.process_contexts.process_contexts_for_user()
                else:
                    contexts = []

            try:
                # Skip user input if auto_submit is set
                if self.session.get_flag('auto_submit'):
                    self.session.set_flag('auto_submit', False)
                    user_input = ""
                else:
                    user_input = self.get_user_input()

                self.utils.output.write()

                # Safe action call with None check
                user_commands_action = self.session.get_action('user_commands')
                if user_commands_action and user_commands_action.run(user_input):
                    continue

            # handle Ctrl-C
            except (KeyboardInterrupt, EOFError):
                try:
                    if not self.session.handle_exit():
                        self.utils.tab_completion.run('chat')
                        continue
                    raise
                except (KeyboardInterrupt, EOFError):
                    raise

            self.chat.add(user_input, 'user', contexts)

            # Clear temporary contexts
            for context_type in list(self.session.context.keys()):
                if context_type not in ('prompt', 'chat'):
                    self.session.remove_context_type(context_type)

            self.handle_assistant_response()
            self.utils.output.write()
