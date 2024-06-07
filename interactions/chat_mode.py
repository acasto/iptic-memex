import time
from session_handler import InteractionHandler


class ChatMode(InteractionHandler):
    """
    Interaction handler for ask mode
    This interaction handler is used when the user wants to ask questions about a file or URL. It will
    put together the necessary context and then ask for the users input.
    """

    def __init__(self, session, provider):
        self.conf = session.get_session_settings()
        self.provider = provider

        # set the system prompt
        if 'prompt' not in self.conf['loadctx']:  # get the default prompt if needed
            session.add_context('prompt')
        self.conf['system_prompt'] = ''
        for p in self.conf['loadctx']['prompt']:
            prompt = p.start()
            self.conf['system_prompt'] += prompt['content']

    def start(self):
        # get files if needed
        if 'file' in self.conf['loadctx']:
            self.conf['file'] = "<|project_context|>"
            # go through each file and place the contents in tags in the format
            # <|project_context|><|file:file_name|>{file content}<|end_file|><|end_project_context|>
            for f in self.conf['loadctx']['file']:
                file = f.start()
                print(f"Loading file: {file['name']}", end='\n')
                self.conf['file'] += f"<|file:{file['name']}|>{file['content']}<|end_file|>"
            self.conf['file'] += "<|end_project_context|>"
            print()

        # Get the users input
        question = ''
        if 'file' in self.conf:
            question += self.conf['file']
        question += input("You: ")
        print()

        message = [
            {'role': 'system', 'content': self.conf['system_prompt']},
            {'role': 'user', 'content': question}
        ]

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

        print()
        # activity = self.provider.get_usage()
        # if activity:
        #     print()
        #     print(f"Tokens: {activity}")
        #     print()
