import os
import openai
from openai import OpenAI
from session_handler import APIProvider, SessionHandler


class OpenAIProvider(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session: SessionHandler):
        self.usage = None
        self.conf = session.get_session_settings()
        if 'api_key' in self.conf['parms']:
            self.api_key = self.conf['parms']['api_key']
        else:
            self.api_key = os.environ['OPENAI_API_KEY']
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

    def chat(self, context: dict):
        """
        Creates a chat completion request to the OpenAI API
        :param context:
        :return: response (str)
        """
        try:
            # Assemble the message from the context
            messages = self.assemble_message(context)

            # Loop through the parameters and add them to the list if they are available
            api_parms = {}
            for parameter in self.parameters:
                if parameter in self.conf['parms'] and self.conf['parms'][parameter] is not None:
                    api_parms[parameter] = self.conf['parms'][parameter]

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

    def stream_chat(self, context):
        """
        Use generator chaining to keep the response provider-agnostic
        :param context:
        :return:
        """
        response = self.chat(context)
        for event in response:
            if event.choices and len(event.choices) > 0:
                if event.choices[0].finish_reason != 'stop':
                    yield event.choices[0].delta.content  # return the content delta
            if event.usage is not None:
                self.usage = event.usage

    @staticmethod
    def assemble_message(context) -> list:
        """
        Assemble the message from the context
        :param context:
        :return: message (str)
        """
        prompt = ''
        chat = None
        if 'prompt' in context:
            for p in context['prompt']:  # there could be multiple prompts
                prompt += p.get()['content']
        if 'chat' in context:
            chat = context['chat'][0]  # there should only be one chat in context
        message = []
        if prompt:
            message.append({'role': 'system', 'content': prompt})
        if chat is not None:
            turn_context = ''
            for turn in chat.get():  # go through each turn in the conversation
                # if context is in turn and not an empty list
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

    def get_usage(self):
        if self.usage is not None:
            return {
                'in': self.usage.prompt_tokens,
                'out': self.usage.completion_tokens,
                'total': self.usage.total_tokens
            }
