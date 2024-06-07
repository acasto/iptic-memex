import time
from session_handler import InteractionHandler


class CompletionMode(InteractionHandler):
    """
    Completion mode interaction handler
    This interaction runs a completion based on the file content provided via the command line
    For example: echo "Say: Hello, World!" | python main.py -f -
    """

    def __init__(self, session, provider):
        self.conf = session.get_session_settings()
        self.provider = provider
        # get the file object from the session settings
        self.file = self.conf['loadctx']['file'][0].start()['content']

    def start(self):
        """
        Start the completion mode interaction
        """
        message = [{'role': 'user', 'content': self.file}]

        # if we are in stream mode, iterate through the stream of events
        if self.conf['parms']['stream'] is True:
            response = self.provider.stream_chat(message)
            # iterate through the stream of events, add in a delay to simulate a more natural conversation
            if response:
                for i, event in enumerate(response):
                    print(event, end='', flush=True)
                    if 'stream_delay' in self.conf['parms']:
                        time.sleep(float(self.conf['parms']['stream_delay']))
            print()

        # else just print the response
        else:
            print(self.provider.chat(message))

        # activity = self.provider.get_usage()
        # if activity:
        #     print()
        #     print(f"Tokens: {activity}")
        #     print()
