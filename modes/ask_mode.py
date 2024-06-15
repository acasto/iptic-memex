import time
from session_handler import InteractionMode


class AskMode(InteractionMode):
    """
    Interaction handler for ask mode
    This interaction handler is used when the user wants to ask questions about a file or URL. It will
    put together the necessary context and then ask for the users input.
    """

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()

        # Initialize a chat context object
        session.add_context('chat')  # initialize a chat context object
        self.chat = session.get_context('chat')  # get the chat context object

    def start(self):
        # get the labels
        user_label = self.params['user_label']
        response_label = self.params['response_label']

        # Get any files that came in from the CLI to add to the message context
        contexts = self.session.get_context('file')

        # Let the user know what file(s) we are working with
        if contexts and len(contexts) > 0:
            print()
            for context in contexts:
                print(f"In context: {context.get()['name']}")
            print()

        # Get the users input
        question = input(f"{user_label} ")
        print()

        # Add the question to the chat context
        self.chat.add(question, 'user', contexts)

        print(f"{response_label} ", end='', flush=True)
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

        print()
        # activity = self.provider.get_usage()
        # if activity:
        #     print()
        #     print(f"Tokens: {activity}")
        #     print()
