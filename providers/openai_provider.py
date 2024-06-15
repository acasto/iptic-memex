import os
import openai
from openai import OpenAI
from session_handler import APIProvider, SessionHandler


class OpenAIProvider(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        if 'api_key' in self.params and self.params['api_key'] is not None:
            self.api_key = self.params['api_key']
        else:
            try:
                self.api_key = os.environ['OPENAI_API_KEY']
            except KeyError:
                print("No API key found in the environment or the session parameters")
                quit()
        self.client = OpenAI(api_key=self.api_key)

        # List of parameters that can be passed to the OpenAI API that we want to handle automatically
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
            'stream',
            'temperature',
            'top_p',
            'tools',
            'tool_choice',
            'user',
        ]

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

            # Loop through the parameters and add them to the list if they are available
            api_parms = {}
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # Add the messages to the API parameters
            api_parms['messages'] = messages

            # If streaming set stream_options
            if 'stream' in api_parms and api_parms['stream'] is True:
                api_parms['stream_options'] = {
                    'include_usage': True,
                }

            # Call the OpenAI API
            response = self.client.chat.completions.create(**api_parms)

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response.usage is not None:
                    self.usage = response.usage
                return response.choices[0].message.content

        except openai.APIConnectionError as e:
            print("The server could not be reached")
            print(e.__cause__)  # an underlying Exception, likely raised within httpx.
        except openai.RateLimitError:
            print("A 429 status code was received; we should back off a bit.")
        except openai.APIStatusError as e:
            print("Another non-200-range status code was received")
            print(e.status_code)
            print(e.response)

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()
        for chunk in response:
            if chunk.choices and len(chunk.choices) > 0:
                if chunk.choices[0].finish_reason != 'stop' and chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content  # return the content delta
            if chunk.usage is not None:
                self.usage = chunk.usage

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
                # todo: eventually we might want to differentiate between different types of context objects
                if 'context' in turn and turn['context']:
                    turn_context += "<|project_context|>"
                    # go through each object and place the contents in tags in the format:
                    # <|project_context|><|file:file_name|>{file content}<|end_file|><|end_project_context|>
                    for f in turn['context']:
                        file = f.get()
                        turn_context += f"<|file:{file['name']}|>{file['content']}<|end_file|>"
                    turn_context += "<|end_project_context|>"

                message.append({'role': turn['role'], 'content': turn_context + "\n" + turn['message']})
        return message

    def get_messages(self):
        return self.assemble_message()

    def get_usage(self):
        if self.usage is not None:
            return {
                'in': self.usage.prompt_tokens,
                'out': self.usage.completion_tokens,
                'total': self.usage.total_tokens
            }
