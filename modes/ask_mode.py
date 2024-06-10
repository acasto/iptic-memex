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
        self.conf = session.get_session_settings()
        self.provider = session.get_provider()

        # Initialize a chat context object
        self.session.add_context('chat')  # initialize a chat context object
        self.chat = self.conf['loadctx']['chat'][0]  # get the chat context object

    def start(self):
        # Get any files that came in from the CLI to add to the message context
        contexts = []
        if 'file' in self.conf['loadctx']:  # todo: we'll need to revisit this with additional contexts
            contexts.extend(self.conf['loadctx']['file'])
            self.conf['loadctx'].pop('file')  # remove the file from context

        # Let the user know what file(s) we are working with
        if len(contexts) > 0:
            print()
            for context in contexts:
                print(f"In context: {context.get()['name']}")
            print()

        # get the labels
        user_label = self.session.get_label('user')
        response_label = self.session.get_label('response')

        # Get the users input
        question = input(f"{user_label} ")
        print()

        # Add the question to the chat context
        self.chat.add(question, 'user', contexts)

        print(f"{response_label} ", end='', flush=True)
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

        print()
        # activity = self.provider.get_usage()
        # if activity:
        #     print()
        #     print(f"Tokens: {activity}")
        #     print()
