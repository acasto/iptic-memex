from session_handler import InteractionAction


class PrintResponseAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.chat = session.get_context('chat')
        self.ui = session.get_action('ui')
        self.sc = session.get_action('assistant_subcommands')

    def run(self):
        """
        Print the response to the user and process it for commands
        """
        # Refresh the params
        self.params = self.session.get_params()

        # Start the response
        response_label = self.ui.color_wrap(self.params['response_label'],
                                            self.params['response_label_color'])
        self.session.utils.output.write(f"{response_label} ", end='', flush=True)

        # Get response from provider (streaming or regular)
        if self.params['stream'] is True:
            try:
                stream = self.session.get_provider().stream_chat()
                if stream:
                    response = self.session.utils.stream.process_stream(stream)
                else:
                    return
            except (KeyboardInterrupt, EOFError):
                self.session.utils.output.write('')  # newline
                return
        else:
            try:
                response = self.session.get_provider().chat()
                if response is None:
                    return
                self.session.utils.output.write(response)
                self.session.utils.output.write('')  # newline
            except (KeyboardInterrupt, EOFError):
                self.session.utils.output.write('')  # newline
                return

        # Add to chat context and process subcommands
        self.chat.add(response, 'assistant')
        self.sc.run(response)
