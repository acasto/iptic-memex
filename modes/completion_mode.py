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
        self.session.set_flag('completion_mode', True)

        contexts = self.session.get_action('process_contexts').get_contexts(self.session)
        stdin_context = next((c for c in contexts if c['context'].get()['name'] == 'stdin'), None)

        # Only remove prompt if stdin is present and prompt wasn't explicitly set by user
        if stdin_context and 'prompt' not in session.user_options:
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
        """Start the completion mode interaction."""
        import time

        if self.params.get('raw_completion'):
            # Raw mode: always non-streaming, emit raw JSON
            self.params['stream'] = False
            self.session.get_provider().chat()
            print(self.session.get_provider().get_full_response())
        else:
            # Normal completion: stream only if CLI flag used
            stream = self.params.get('stream_completion', False)
            self.params['stream'] = stream

            if stream:
                response = self.session.get_provider().stream_chat()
                if response:
                    for chunk in response:
                        print(chunk, end='', flush=True)
                        if 'stream_delay' in self.params:
                            time.sleep(float(self.params['stream_delay']))
                print()
            else:
                print(self.session.get_provider().chat())
