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
        if 'api_key' in self.conf:
            self.api_key = self.conf['api_key']
        else:
            self.api_key = os.environ['OPENAI_API_KEY']
        self.client = OpenAI(api_key=self.api_key)

    def chat(self, messages):
        """
        Creates a chat completion request to the OpenAI API
        :param messages: the message to complete from an interaction handler
        :return: response (str)
        """
        try:
            # Build the parameters list
            openai_parms = {
                'model': self.conf['parms']['model_name'],
                'messages': messages,
            }
            # Add temperature and max_tokens to the parameters list if they are available
            if 'stream' in self.conf['parms'] and (self.conf['parms']['stream'] == 'True' or self.conf['parms']['stream'] == 'true'):
                 openai_parms['stream'] = True
            if self.conf['parms']['temperature'] is not None:
                openai_parms['temperature'] = float(self.conf['parms']['temperature'])
            if self.conf['parms']['max_tokens'] is not None:
                openai_parms['max_tokens'] = int(self.conf['parms']['max_tokens'])

            response = self.client.chat.completions.create(**openai_parms)
            # if in stream mode chain the generator
            if 'stream' in openai_parms and openai_parms['stream'] is True:
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
