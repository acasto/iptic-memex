import time
from session_handler import InteractionMode


class CompletionMode(InteractionMode):
    """
    Completion mode interaction handler
    This interaction runs a completion based on the file content provided via the command line
    For example: echo "Say: Hello, World!" | python main.py -f -
    """

    def __init__(self, session):
        self.conf = session.get_session_settings()
        self.provider = session.get_provider()

        # Since in this interaction we want the file contents to serve as the prompt we'll remove the default
        # prompt and initialize a chat context object and add the file contents to it
        #
        self.conf['loadctx'].pop('prompt', None)  # get rid of the default prompt
        session.add_context('chat')  # initialize a chat context object
        self.chat = self.conf['loadctx']['chat'][0]  # get the chat context object
        file = self.conf['loadctx']['file'][0].get()['content']  # get the file contents
        self.chat.add(file)  # add the file contents as the user input

    def start(self):
        """
        Start the completion mode interaction
        """
        # if we are in stream mode, iterate through the stream of events
        if self.conf['parms']['stream'] is True:
            response = self.provider.stream_chat(self.conf['loadctx'])
            # iterate through the stream of events, add in a delay to simulate a more natural conversation
            if response:
                for i, event in enumerate(response):
                    print(event, end='', flush=True)
                    if 'stream_delay' in self.conf['parms']:
                        time.sleep(float(self.conf['parms']['stream_delay']))
            print()

        # else just print the response
        else:
            print(self.provider.chat(self.conf['loadctx']))

        # activity = self.provider.get_usage()
        # if activity:
        #     print()
        #     print(f"Tokens: {activity}")
        #     print()
