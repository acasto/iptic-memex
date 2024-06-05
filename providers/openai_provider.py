import os
import openai
from openai import OpenAI
from session_handler import APIProvider


class OpenAIHandler(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, conf):
        self.conf = conf
        if 'api_key' in conf:
            self.api_key = conf['api_key']
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
            response = self.client.chat.completions.create(
                model=self.conf['model'],
                messages=messages,
                temperature=float(self.conf['temperature']),
                max_tokens=int(self.conf['max_tokens']),
                stream=bool(self.conf['stream']),
            )
            # if in stream mode chain the generator
            if self.conf['stream']:
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
