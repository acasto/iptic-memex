import time
from session_handler import InteractionMode


class CompletionMode(InteractionMode):
    """
    Completion mode interaction handler
    This interaction runs a completion based on the file content provided via the command line
    For example: echo "Say: Hello, World!" | python main.py -f -
    """

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()

        # Since in this interaction we want the file contents to serve as the prompt we'll remove the default
        # prompt and initialize a chat context object and add the file contents to it
        #
        session.remove_context('prompt')  # get rid of the default prompt
        session.add_context('chat')  # initialize a chat context object
        self.chat = self.session.get_context('chat')  # get the chat context object
        self.chat.add(session.get_context('file')[0].get()['content'])  # add the file contents as the user input

    def start(self):
        """
        Start the completion mode interaction
        """
        # print(self.session.get_session_state())
        # quit()
        # if we are in stream mode, iterate through the stream of events
        if self.params['stream'] is True:
            response = self.session.get_provider().stream_chat()
            # iterate through the stream of events, add in a delay to simulate a more natural conversation
            if response:
                for i, event in enumerate(response):
                    print(event, end='', flush=True)
                    if 'stream_delay' in self.params:
                        time.sleep(float(self.params['stream_delay']))
            print()

        # else just print the response
        else:
            print(self.session.get_provider().chat())
