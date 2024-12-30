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

        # Format any remaining files if they exist
        content = ""
        if contexts:
            content = self.session.get_action('process_contexts').process_contexts_for_assistant(contexts)
            if stdin_context:
                content += "\n"

        # Append stdin content if present
        if stdin_context:
            content += stdin_context['context'].get()['content']

        self.chat.add(content)

    def start(self):
        """
        Start the completion mode interaction
        """
        # print(self.session.get_session_state())
        # quit()
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
