import os
import anthropic
from session_handler import APIProvider


class AnthropicHandler(APIProvider):
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
