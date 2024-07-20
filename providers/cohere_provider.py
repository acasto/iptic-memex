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

        # set the options for the OpenAI API client
        options = {}
        if 'api_key' in self.params and self.params['api_key'] is not None:
            options['api_key'] = self.params['api_key']
        else:
            print(f"\nAPI key not found for Cohere provider in configuration.\n")
            quit()

        # Initialize the Cohere client
        self.client = cohere.Client(**options)

        # List of parameters that can be passed to the OpenAI API that we want to handle automatically
        # todo: add list of items for include/exclude to the providers config
        self.parameters = [
            'model',
            'messages',
            'max_tokens',
            'frequency_penalty',
            'logit_bias',
            'logprobs',
            'top_logprobs',
            'n',
            'presence_penalty',
            'response_format',
            'seed',
            'stop',
            'temperature',
            'top_p',
            'tools',
            'tool_choice',
            'user',
            'extra_body'
        ]

        # place to store usage data
        self.usage = None

    def chat(self):
        try:
            # Assemble the message and chat history from the context
            chat_history, message = self.assemble_message()

            # Loop through the parameters and add them to the list if they are available
            api_parms = {}
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # Add the chat_history and message to the API parameters
            api_parms['chat_history'] = chat_history
            api_parms['message'] = message

            # Save the params for debugging
            self.last_api_param = api_parms

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                # Call the Cohere API
                response = self.client.chat_stream(**api_parms)
                return response
            else:
                # Call the Cohere API
                response = self.client.chat(**api_parms)
                # if response.usage is not None:
                #     self.usage = response.usage
                return response.text

        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
            return "I'm sorry, but an error occurred while processing your request."

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()

        if response is None:
            return

        for event in response:
            yield event
            # if event.event_type == "text-generation":
            #     yield event.text
            # if event.event_type == "stream-end":
            #     return
            # if chunk.usage is not None:
            #     self.usage = chunk.usage

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
