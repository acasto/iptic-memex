import os
import openai
from openai import OpenAI
from session_handler import APIProvider, SessionHandler


class OpenAIProvider(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session: SessionHandler):
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
            'stream_options'
            'temperature',
            'top_p',
            'tools',
            'tool_choice',
            'user',
        ]

    def chat(self, messages):
        """
        Creates a chat completion request to the OpenAI API
        :param messages: the message to complete from an interaction handler
        :return: response (str)
        """
        try:
            self.conf['parms']['messages'] = messages  # add the messages to the parms so we can loop
            openai_parms = {}
            # Loop through the parameters and add them to the list if they are available
            for parameter in self.parameters:
                if parameter in self.conf['parms'] and self.conf['parms'][parameter] is not None:
                    openai_parms[parameter] = self.conf['parms'][parameter]

            # unset stream if it's false
            if 'stream' in openai_parms and openai_parms['stream'] == 'False':
                del openai_parms['stream']

            # print(openai_parms)
            # quit()

            response = self.client.chat.completions.create(**openai_parms)
            # if in stream mode chain the generator
            if 'stream' in openai_parms and openai_parms['stream'] == 'True':
                return response
            else:
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

    def stream_chat(self, messages):
        """
        Use generator chaining to keep the response provider-agnostic
        :param messages:
        :return:
        """
        response = self.chat(messages)
        for event in response:
            if hasattr(event.choices[0].delta, 'content'):
                if event.choices[0].delta.content is not None:
                    yield event.choices[0].delta.content
