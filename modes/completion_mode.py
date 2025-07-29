from session_handler import InteractionMode


class CompletionMode(InteractionMode):
    """
    Completion mode interaction handler
    This interaction runs a completion based on the file content provided via the command line
    For example: echo "Say: Hello, World!" | python main.py -f -
    """

    def __init__(self, session):
        self.session = session
        
        self.params = self.session.get_params()
        self.session.set_flag('completion_mode', True)

        # Skip complex context processing for now - just add chat context
        self.session.add_context('chat')
        self.chat = self.session.get_context('chat')

        # For now, just add an empty message to start the chat
        if self.chat and hasattr(self.chat, 'add'):
            self.chat.add("", 'user', [])

    def start(self):
        """Start the completion mode interaction."""
        import time

        provider = self.session.get_provider()
        if not provider:
            print("No provider available")
            return

        if self.params.get('raw_completion'):
            # Raw mode: always non-streaming, emit raw JSON
            self.params['stream'] = False
            provider.chat()
            if hasattr(provider, 'get_full_response'):
                print(provider.get_full_response())
        else:
            # Normal completion: stream only if CLI flag used
            stream = self.params.get('stream_completion', False)
            self.params['stream'] = stream

            if stream:
                if hasattr(provider, 'stream_chat'):
                    response = provider.stream_chat()
                    if response:
                        for chunk in response:
                            print(chunk, end='', flush=True)
                            if 'stream_delay' in self.params:
                                time.sleep(float(self.params['stream_delay']))
                    print()
            else:
                if hasattr(provider, 'chat'):
                    response = provider.chat()
                    if response:
                        print(response)