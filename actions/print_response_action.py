import time
from session_handler import InteractionAction


class PrintResponseAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.chat = session.get_context('chat')
        self.ui = session.get_action('ui')

    def run(self):
        """
        Print the response to the user
        """
        # Refresh the params
        self.params = self.session.get_params()
        # Start the response
        response_label = self.ui.color_wrap(self.params['response_label'], self.params['response_label_color'])
        print(f"{response_label} ", end='', flush=True)
        # if we are in stream mode, iterate through the stream of events
        if self.params['stream'] is True:
            accumulator = ''
            try:  # allow the user to interrupt the stream with ctrl-c
                response = self.session.get_provider().stream_chat()
                # iterate through the stream of events, add in a delay to simulate a more natural conversation
                if response:
                    for i, event in enumerate(response):
                        print(event, end='', flush=True)
                        accumulator += event  # accumulate the response so we can save it.
                        if 'stream_delay' in self.params:
                            time.sleep(float(self.params['stream_delay']))
            except (KeyboardInterrupt, EOFError):  # allow the user to interrupt the stream with ctrl-c
                pass  # do nothing, just on now that we've interrupted the stream
            print()
            self.chat.add(accumulator, 'assistant')

        # else just print the response
        else:
            try:
                response = self.session.get_provider().chat()
                print(response)
                self.chat.add(response, 'assistant')
            except (KeyboardInterrupt, EOFError):
                print()
