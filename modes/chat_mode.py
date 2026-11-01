from base_classes import InteractionMode
from app.chat import configure_chat, run_chat_turn
from core.runtime.interaction import InteractionService
from ui.interaction_handler import CLIInteractionHandler
from ui.completion_renderer import CompletionRenderer
from utils.output_utils import format_cli_label


class ChatMode(InteractionMode):
    def __init__(self, session, builder=None):
        self.session = session
        self.builder = builder  # For model switching
        
        # Don't cache params - get them fresh each time
        self.utils = self.session.utils
        configure_chat(self.session)
        self.output = getattr(self.utils.output, 'idle_output', None) or self.utils.output
        self.interactions = InteractionService(CLIInteractionHandler(self.utils.input, self.output))

        if self.session.get_context('chat') is None:
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
        params = self.params
        user_label = format_cli_label(params['user_label'])
        prompt = self.utils.output.style_text(
            user_label,
            fg=params['user_label_color']
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

            # Presentation belongs to the CLI; execution attaches contexts silently.
            if self.process_contexts:
                self.process_contexts.process_contexts_for_user(auto_submit=False)

            try:
                user_input = self.get_user_input()

                self.utils.output.write()

                # Safe action call with None check
                user_commands_action = self.session.get_action('chat_commands')
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

            # Execution owns tool follow-ups; the CLI only renders events/results.
            params = self.params
            stream = bool(params.get('stream'))

            # For streaming, print the assistant label before tokens
            raw_response_label = format_cli_label(params['response_label'])
            response_label = self.utils.output.style_text(
                raw_response_label,
                fg=params['response_label_color']
            )

            if stream:
                self.utils.output.write(f"{response_label} ", end='', flush=True)

            renderer = CompletionRenderer(self.output.write, self.output.warning)
            result = run_chat_turn(self.session, user_input, on_event=renderer,
                                   interactions=self.interactions)
            if result.status != 'completed':
                self.output.warning(result.error or f'Run {result.status}')

            if not stream:
                # Non-stream: print label and the filtered display text
                self.utils.output.write(f"{response_label} ", end='', flush=True)
                if result.last_text:
                    self.utils.output.write(result.last_text)
                self.utils.output.write('')

            # Spacer between turns
            self.utils.output.write()
