from session_handler import InteractionMode


class ChatMode(InteractionMode):
    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.utils = session.utils

        session.add_context('chat')
        self.chat = session.get_context('chat')
        self.subcommands = session.get_action('assistant_subcommands')
        self.process_contexts = session.get_action('process_contexts')

    def get_user_input(self):
        """Handle multiline user input with continuation support"""
        first_line = True
        user_input = []

        while True:
            prompt = self.utils.output.style_text(self.params['user_label'],
                                                  fg=self.params['user_label_color']) + " " if first_line else ""
            line = self.utils.input.get_input(prompt)
            first_line = False

            if line.rstrip() == r"\\":
                break
            elif line.endswith("\\"):
                user_input.append(line[:-1] + "\n")
            else:
                user_input.append(line)
                break

        return "".join(user_input).rstrip()

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
                response = self.utils.stream.process_stream(stream, spinner_message="Thinking...")
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
        self.subcommands.run(response)
        return response

    def start(self):
        """Start the chat interaction loop"""
        self.utils.tab_completion.run('chat')

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

                if self.session.get_action('process_subcommands').run(user_input):
                    continue

            except (KeyboardInterrupt, EOFError):
                try:
                    self.utils.input.get_input(
                        self.utils.output.style_text("Hit Ctrl-C again to quit or Enter to continue.", fg='red'),
                        spacing=1
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
