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

        contexts = self.session.get_action('process_contexts').get_contexts(self.session)
        stdin_context = next((c for c in contexts if c['context'].get()['name'] == 'stdin'), None)

        # Only remove prompt if stdin is present
        if stdin_context:
            session.remove_context_type('prompt')
            # Remove stdin from contexts
            contexts.remove(stdin_context)

        session.add_context('chat')
        self.chat = session.get_context('chat')

        # Add the message with all contexts included
        if stdin_context:
            self.chat.add(stdin_context['context'].get()['content'], 'user', contexts)
        elif contexts:
            self.chat.add("", 'user', contexts)

    def start(self):
        """Start the completion mode interaction"""
        if self.params.get('raw_completion'):
            # Force disable streaming for raw output
            self.params['stream'] = False
            # Get response but don't print it
            self.session.get_provider().chat()
            # Get and print the raw response
            raw_response = self.session.get_provider().get_full_response()
            print(raw_response)
        else:
            # Existing completion logic
            if self.params['stream'] is True:
                response = self.session.get_provider().stream_chat()
                if response:
                    for i, event in enumerate(response):
                        print(event, end='', flush=True)
                        if 'stream_delay' in self.params:
                            time.sleep(float(self.params['stream_delay']))
                print()
            else:
                print(self.session.get_provider().chat())
