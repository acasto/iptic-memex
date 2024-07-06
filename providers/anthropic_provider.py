import os
from anthropic import Anthropic
from session_handler import APIProvider, SessionHandler


class AnthropicProvider(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()

        # set the options for the OpenAI API client
        options = {}
        if 'api_key' in self.params and self.params['api_key'] is not None:
            options['api_key'] = self.params['api_key']
        elif 'ANTHROPIC_API_KEY' in os.environ:
            options['api_key'] = os.environ['ANTHROPIC_API_KEY']
        else:
            options['api_key'] = 'none'  # in case we're using the library for something else but still need something set

        if 'base_url' in self.params and self.params['base_url'] is not None:
            options['base_url'] = self.params['base_url']

        # Initialize the OpenAI client
        self.client = Anthropic(**options)

        # List of parameters that can be passed to the API that we want to handle automatically
        self.parameters = [
            'model',
            'system',
            'messages',
            'max_tokens',
            'stop_sequences',
            'metadata',
            'stream',
            'temperature',
            'top_k',
            'top_p',
            'tools',
            'tool_choice',

        ]

        # place to store usage data
        self.usage = {}

    def chat(self):
        """
        Creates a chat completion request to the OpenAI API
        :return: response (str)
        """
        try:
            # Assemble the message from the context
            messages = self.assemble_message()

            # Loop through the parameters and add them to the list if they are available
            api_parms = {}
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # Anthropic takes the system prompt as a top level parameter
            if self.session.get_context('prompt'):
                # if prompt content is not empty, add it to the API parameters
                if self.session.get_context('prompt').get()['content']:
                    api_parms['system'] = self.session.get_context('prompt').get()['content']

            # Add the messages to the API parameters
            api_parms['messages'] = messages

            # Call the Anthropic API
            response = self.client.messages.create(**api_parms)

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response.usage is not None:
                    self.usage['in'] = response.useage.input_tokens
                    self.usage['out'] = response.usage.output_tokens
                return response.content[0].text

        finally:
            pass

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()
        for event in response:
            if event.type == "content_block_delta":
                yield event.delta.text
            if event.type == "message_start":
                if event.message.usage is not None:
                    self.usage['in'] = event.message.usage.input_tokens
                    self.usage['out'] = event.message.usage.output_tokens
            if event.type == "message_delta":
                if event.usage is not None:
                    self.usage['out'] = self.usage['out'] + event.usage.output_tokens

    def assemble_message(self) -> list:
        """
        Assemble the message from the context
        :return: message (str)
        """
        message = []

        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():  # go through each turn in the conversation
                turn_context = ''
                # if context is in turn and not an empty list
                if 'context' in turn and turn['context']:
                    turn_context += "<|project_context|>"
                    # go through each object and place the contents in tags in the format:
                    # <|project_context|><|file:file_name|>{file content}<|end_file|><|end_project_context|>
                    for f in turn['context']:
                        file = f['context'].get()
                        turn_context += f"<|file:{file['name']}|>{file['content']}<|end_file|>"
                    turn_context += "<|end_project_context|>"

                message.append({'role': turn['role'], 'content': turn_context + "\n" + turn['message']})
        return message

    def get_messages(self):
        return self.assemble_message()

    def get_usage(self):
        if self.usage is not None:
            return {
                'in': self.usage['in'],
                'out': self.usage['out'],
                'total': self.usage['in'] + self.usage['out']
            }
        else:
            return "No usage data available"

    def reset_usage(self):
        self.usage = {}
