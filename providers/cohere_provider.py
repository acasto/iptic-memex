import cohere
from session_handler import APIProvider, SessionHandler


class CohereProvider(APIProvider):
    """
    Cohere API handler
    """

    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        self.last_api_param = None

        # set the options for the Cohere API client
        options = {}
        if 'api_key' in self.params and self.params['api_key'] is not None:
            options['api_key'] = self.params['api_key']
        else:
            print(f"\nAPI key not found for Cohere provider in configuration.\n")
            quit()

        # Initialize the Cohere client
        self.client = cohere.Client(**options)

        # List of parameters that can be passed to the Cohere API that we want to handle automatically
        self.parameters = [
            'model',
            'max_tokens',
            'max_input_tokens',
            'top-p',
            'top-k',
            'k',
            'p',
            'stop_sequences',
            'frequency_penalty',
            'presence_penalty',
            'seed',
            'temperature',
            'tools',
            'tools_results',
        ]

        # place to store usage data
        self.usage = None

    def process_api_params(self):
        chat_history, message = self.assemble_message()

        api_params = {}
        for parameter in self.parameters:
            if parameter in self.params and self.params[parameter] is not None:
                api_params[parameter] = self.params[parameter]

        api_params['chat_history'] = chat_history
        api_params['message'] = message

        self.last_api_param = api_params
        return api_params

    def chat(self):
        try:
            api_params = self.process_api_params()
            response = self.client.chat(**api_params)
            return response.text
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
            return "I'm sorry, but an error occurred while processing your request."

    def stream_chat(self):
        try:
            api_params = self.process_api_params()
            response = self.client.chat_stream(**api_params)
            for event in response:
                if event.event_type == "text-generation":
                    yield event.text
                if event.event_type == "stream-end":
                    return
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
            yield "I'm sorry, but an error occurred while processing your request."

    def assemble_message(self):
        """
        Assemble the message and chat history from the context
        :return: tuple (chat_history: list, message: str)
        """
        chat_history = []
        message = ""

        if self.session.get_context('prompt'):
            chat_history.append({'role': 'System', 'message': self.session.get_context('prompt').get()['content']})

        chat = self.session.get_context('chat')
        if chat is not None:
            for idx, turn in enumerate(chat.get()):
                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])

                content = turn_context + "\n" + turn['message']
                role = 'User' if turn['role'].lower() == 'user' else 'Chatbot'
                chat_history.append({'role': role, 'message': content})

        if chat_history:
            message = chat_history[-1]['message']
            chat_history = chat_history[:-1]  # Remove the last message from chat_history

        return chat_history, message

    def get_messages(self):
        return self.assemble_message()

    def get_usage(self):
        if self.usage is not None:
            return {
                'in': self.usage.prompt_tokens,
                'out': self.usage.completion_tokens,
                'total': self.usage.total_tokens
            }

    def reset_usage(self):
        self.usage = None
