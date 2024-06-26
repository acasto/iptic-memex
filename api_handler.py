import os
import openai
from openai import OpenAI
import anthropic
import google.generativeai as genai
from token_counter import TikTokenCounter
from abc import ABC, abstractmethod


class APIHandler(ABC):
    """
    Abstract class for API handlers
    """

    @abstractmethod
    def chat(self, message):
        pass

    @abstractmethod
    def stream_chat(self, message):
        pass


# noinspection PyTypeChecker
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
        self.client = OpenAI(api_key=self.api_key)
        self.token_counter = TikTokenCounter()

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

    def count_tokens(self, messages, model):
        """
        Counts the number of tokens in a message
        :param messages: the message to count tokens from
        :param model: the model being used
        :return: number of tokens (int)
        """
        return self.token_counter.count_tokens(messages, model)


class AnthropicHandler(APIHandler):
    """
    OpenAI API handler
    """

    def __init__(self, conf):
        self.conf = conf
        if 'api_key' in conf:
            self.api_key = conf['api_key']
        else:
            self.api_key = os.environ['ANTHROPIC_API_KEY']
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def chat(self, messages):
        """
        Creates a chat completion request to the OpenAI API
        :param messages: the message to complete from an interaction handler
        :return: response (str)
        """
        try:
            response = self.client.messages.create(
                model=self.conf['model'],
                system=messages[0]['content'],
                messages=self.merge_consecutive_users(messages[1:]),
                temperature=float(self.conf['temperature']),
                max_tokens=int(self.conf['max_tokens']),
                stream=bool(self.conf['stream']),
            )
            # if in stream mode chain the generator
            if self.conf['stream']:
                return response
            else:
                # return response.content as a string
                return response.content[0].text

        # except openai.APIConnectionError as e:
        #     print("The server could not be reached")
        #     print(e.__cause__)  # an underlying Exception, likely raised within httpx.
        # except openai.RateLimitError:
        #     print("A 429 status code was received; we should back off a bit.")
        # except openai.APIStatusError as e:
        #     print("Another non-200-range status code was received")
        #     print(e.status_code)
        #     print(e.response)
        finally:
            pass


    def stream_chat(self, messages):
        """
        Use generator chaining to keep the response provider-agnostic
        :param messages:
        :return:
        """
        response = self.chat(messages)
        for event in response:
            if event.type == "content_block_delta":
                yield event.delta.text

    @staticmethod
    def merge_consecutive_users(messages):
        merged_messages = []
        i = 0
        while i < len(messages):
            if messages[i]['role'] == 'user':
                # Start merging content
                merged_content = messages[i]['content']
                i += 1
                while i < len(messages) and messages[i]['role'] == 'user':
                    merged_content += ' ' + messages[i]['content']
                    i += 1
                merged_messages.append({'role': 'user', 'content': merged_content})
            else:
                merged_messages.append(messages[i])
                i += 1
        return merged_messages


class GoogleHandler(APIHandler):
    """
    OpenAI API handler
    """

    def __init__(self, conf):
        self.conf = conf
        if 'api_key' in conf:
            self.api_key = conf['api_key']
        else:
            self.api_key = os.environ['GOOGLE_API_KEY']
        genai.configure(api_key=self.api_key)
        self.client = genai.GenerativeModel(self.conf['model'])
        self.gchat = self.client.start_chat(history=[])

    def chat(self, messages):
        """
        Creates a chat completion request to the OpenAI API
        :param messages: the message to complete from an interaction handler
        :return: response (str)
        """
        try:
            messages = self.merge_consecutive_users(messages)
            response = self.gchat.send_message(
                messages[-1]['content'],
                stream=bool(self.conf['stream']),
                generation_config=genai.GenerationConfig(
                    temperature=float(self.conf['temperature']),
                    max_output_tokens=int(self.conf['max_tokens']))
            )
            # if in stream mode chain the generator
            if self.conf['stream']:
                return response
            else:
                return response.text
        # except openai.APIConnectionError as e:
        #     print("The server could not be reached")
        #     print(e.__cause__)  # an underlying Exception, likely raised within httpx.
        # except openai.RateLimitError:
        #     print("A 429 status code was received; we should back off a bit.")
        # except openai.APIStatusError as e:
        #     print("Another non-200-range status code was received")
        #     print(e.status_code)
        #     print(e.response)
        finally:
            pass

    def stream_chat(self, messages):
        """
        Use generator chaining to keep the response provider-agnostic
        :param messages:
        :return:
        """
        response = self.chat(messages)
        for event in response:
            yield event.text

    @staticmethod
    def merge_consecutive_users(messages):
        merged_messages = []
        i = 0
        while i < len(messages):
            if messages[i]['role'] == 'user':
                # Start merging content
                merged_content = messages[i]['content']
                i += 1
                while i < len(messages) and messages[i]['role'] == 'user':
                    merged_content += ' ' + messages[i]['content']
                    i += 1
                merged_messages.append({'role': 'user', 'content': merged_content})
            else:
                merged_messages.append(messages[i])
                i += 1
        return merged_messages
