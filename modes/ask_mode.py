import time
from session_handler import InteractionMode


class AskMode(InteractionMode):
    """
    Interaction handler for ask mode
    This interaction handler is used when the user wants to ask questions about a file or URL. It will
    put together the necessary context and then ask for the users input.
    """

    def __init__(self, session):
        self.conf = session.get_session_settings()
        self.provider = session.get_provider()

        # Initialize a chat context object
        session.add_context('chat')  # initialize a chat context object
        self.chat = self.conf['loadctx']['chat'][0]  # get the chat context object

    def start(self):
        # Get the users input
        question = ''
        if 'file' in self.conf:
            question += self.conf['file']
        question += input("You: ")
        print()

        # # get files if needed
        # if 'file' in self.conf['loadctx']:
        #     self.conf['file'] = "<|project_context|>"
        #     # go through each file and place the contents in tags in the format
        #     # <|project_context|><|file:file_name|>{file content}<|end_file|><|end_project_context|>
        #     for f in self.conf['loadctx']['file']:
        #         file = f.get()
        #         print(f"Loading file: {file['name']}", end='\n')
        #         self.conf['file'] += f"<|file:{file['name']}|>{file['content']}<|end_file|>"
        #     self.conf['file'] += "<|end_project_context|>"
        #     print()
        #
        # # Get the users input
        # question = ''
        # if 'file' in self.conf:
        #     question += self.conf['file']
        # question += input("You: ")
        # print()
        contexts = []
        if 'file' in self.conf['loadctx']:
            contexts.extend(self.conf['loadctx']['file'])
        self.chat.add(question, 'user', contexts)

        # print(self.conf['loadctx'])
        # print(self.conf['loadctx']['chat'][0].get())
        # quit()

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
