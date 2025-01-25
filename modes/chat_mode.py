from session_handler import InteractionMode


class ChatMode(InteractionMode):
    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.utils = session.utils

        session.add_context('chat')
        self.chat = session.get_context('chat')
        self.process_contexts = session.get_action('process_contexts')

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

        try:
            if self.params['stream']:
                stream = self.session.get_provider().stream_chat()
                if not stream:
                    return None
                response = self.utils.stream.process_stream(stream, spinner_message="")
            else:
                response = self.session.get_provider().chat()
                if response is None:
                    return None
                self.utils.output.write(response)
                self.utils.output.write('')
        except (KeyboardInterrupt, EOFError):
            self.utils.output.write('')
            return None

        self.chat.add(response, 'assistant')
        self.session.get_action('assistant_commands').run(response)
        return response

    def start(self):
        """Start the chat interaction loop"""
        self.utils.tab_completion.run('chat')
        self.utils.tab_completion.set_session(self.session)

        while True:
            if self.session.get_flag('auto_submit'):
                contexts = self.process_contexts.process_contexts_for_user(auto_submit=True)
            else:
                contexts = self.process_contexts.process_contexts_for_user()

            try:
                # Skip user input if auto_submit is set
                if self.session.get_flag('auto_submit'):
                    self.session.set_flag('auto_submit', False)
                    user_input = ""
                else:
                    user_input = self.get_user_input()

                self.utils.output.write()

                if self.session.get_action('user_commands').run(user_input):
                    continue

            except (KeyboardInterrupt, EOFError):
                try:
                    self.utils.input.get_input(
                        self.utils.output.style_text("Hit Ctrl-C again to quit or Enter to continue.", fg='red'),
                        spacing=1,
                    )
                    self.utils.tab_completion.run('chat')
                    continue
                except (KeyboardInterrupt, EOFError):
                    self.utils.output.write()
                    self.session.get_action('persist_stats').run()
                    raise

            self.chat.add(user_input, 'user', contexts)

            for context_type in list(self.session.get_context().keys()):
                if context_type not in ('prompt', 'chat'):
                    self.session.remove_context_type(context_type)

            self.handle_assistant_response()
            self.utils.output.write()
