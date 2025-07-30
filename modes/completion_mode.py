from base_classes import InteractionMode


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

        # Get all contexts using the process_contexts action
        process_contexts_action = self.session.get_action('process_contexts')
        contexts = process_contexts_action.get_contexts(self.session) if process_contexts_action else []

        # Look for stdin context
        stdin_context = next((c for c in contexts if c['context'].get()['name'] == 'stdin'), None)

        # Only remove prompt if stdin is present and prompt wasn't explicitly set by user
        if stdin_context and 'prompt' not in self.session.config.overrides:
            self.session.remove_context_type('prompt')
            # Remove stdin from contexts so it doesn't appear as file context
            contexts.remove(stdin_context)

        # Add chat context
        self.session.add_context('chat')
        self.chat = self.session.get_context('chat')

        # Add the message with appropriate content
        if stdin_context:
            # stdin content becomes the user message
            self.chat.add(stdin_context['context'].get()['content'], 'user', contexts)
        elif contexts:
            # Files only: empty message with file contexts
            self.chat.add("", 'user', contexts)
        else:
            # No contexts at all
            self.chat.add("Please process the provided content.", 'user', [])

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
