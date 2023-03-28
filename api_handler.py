import os
import openai
import time
from abc import ABC, abstractmethod

class APIHandler(ABC):
    """
    Abstract class for API handlers
    """
    @abstractmethod
    def complete(self, prompt):
        pass

    @abstractmethod
    def stream_complete(self, prompt):
        pass

    @abstractmethod
    def chat(self, message):
        pass

    @abstractmethod
    def stream_chat(self, message):
        pass


class OpenAIHandler(APIHandler):
    """
    OpenAI API handler
    """
    def __init__(self, conf):
        self.conf = conf
        if 'api_key' in conf:
            self.api_key = conf['api_key']
        else:
            self.api_key = os.environ['OPENAI_API_KEY']
        openai.api_key = self.api_key

    def complete(self, prompt):
        """
        Creates a completion request to the OpenAI API
        :param prompt: the prompt to complete from an interaction handler
        :return: response (str)
        """
        response = openai.Completion.create(
            request_timeout=120,
            model=self.conf['model'],
            prompt=prompt,
            temperature=float(self.conf['temperature']),
            max_tokens=int(self.conf['max_tokens']),
            stream=bool(self.conf['stream']),
        )
        # if in stream mode chain the generator
        if self.conf['stream']:
            return response
        else:
            return response.choices[0].text.strip()

    def stream_complete(self, prompt):
        """
        Use generator chaining to keep the response provider-agnostic
        :param prompt:
        :return:
        """
        response =  self.complete(prompt)
        for event in response:
            yield event['choices'][0]['text']

    def chat(self, messages):
        """
        Creates a chat completion request to the OpenAI API
        :param messages: the message to complete from an interaction handler
        :return: response (str)
        """
        response = openai.ChatCompletion.create(
            request_timeout=120,
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
            return response['choices'][0]['message']['content']

    def stream_chat(self, messages):
        """
        Use generator chaining to keep the response provider-agnostic
        :param messages:
        :return:
        """
        response = self.chat(messages)
        for event in response:
            if 'content' in event['choices'][0]['delta']:
                yield event['choices'][0]['delta']['content']

