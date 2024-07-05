import os
import google.generativeai as genai
from session_handler import APIProvider, SessionHandler


class GoogleProvider(APIProvider):
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
        elif 'GOOGLE_API_KEY' in os.environ:
            options['api_key'] = os.environ['GOOGLE_API_KEY']
        else:
            options['api_key'] = 'none'  # in case we're using the library for something else but still need something set

        # Initialize the client
        genai.configure(api_key=options['api_key'])
        self.client = genai.GenerativeModel(self.params['model'])
        self.gchat = self.client.start_chat(history=[])

        # place to store usage data
        self.usage = None

    def chat(self):
        """
        Creates a chat completion request to the OpenAI API
        :return: response (str)
        """
        try:
            # Assemble the message from the context
            messages = self.assemble_message()

            params = {}
            if 'temperature' in self.params:
                params['temperature'] = self.params['temperature']
            else:
                params['temperature'] = 0.7
            if 'max_tokens' in self.params:
                params['max_tokens'] = self.params['max_tokens']
            else:
                params['max_tokens'] = 150

            # Call the Google generative chat API
            response = self.gchat.send_message(
                messages[-1]['content'],
                stream=bool(self.params['stream']),
                generation_config=genai.GenerationConfig(
                    temperature=float(params['temperature']),
                    max_output_tokens=int(params['max_tokens']))
            )

            # if in stream mode chain the generator, else return the text response
            if 'stream' in self.params and self.params['stream'] is True:
                return response
            else:
                return response.text

        finally:
            pass

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()
        for event in response:
            yield event.text

    def assemble_message(self) -> list:
        """
        Assemble the message from the context
        :return: message (str)
        """
        message = []
        if self.session.get_context('prompt'):
            message.append({'role': 'system', 'content': self.session.get_context('prompt').get()['content']})

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
        pass

    def reset_usage(self):
        pass
