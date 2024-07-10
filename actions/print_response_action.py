import time
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
        Print the response to the user and reprint conversation if code block is detected
        """
        # Refresh the params
        self.params = self.session.get_params()
        # Start the response
        response_label = self.ui.color_wrap(self.params['response_label'], self.params['response_label_color'])
        print(f"{response_label} ", end='', flush=True)

        response = ''
        accumulator = ''

        # if we are in stream mode, iterate through the stream of events
        if self.params['stream'] is True:
            try:
                response = self.session.get_provider().stream_chat()
                if response:
                    for event in response:
                        print(event, end='', flush=True)
                        accumulator += event
                        if 'stream_delay' in self.params:
                            time.sleep(float(self.params['stream_delay']))
            except (KeyboardInterrupt, EOFError):
                pass
            print()
            response = accumulator

        # else just print the response
        else:
            try:
                response = self.session.get_provider().chat()
                print(response)
                # if '```' in response:
                #     code_block_detected = True

            except (KeyboardInterrupt, EOFError):
                print()

        # Add the response to the chat and process for subcommands
        self.chat.add(response, 'assistant')
        self.sc.run(response)
