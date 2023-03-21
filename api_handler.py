import os
import openai
from abc import ABC, abstractmethod

class APIHandler(ABC):
    @abstractmethod
    def chat(self, message):
        pass

    @abstractmethod
    def complete(self, prompt):
        pass

class OpenAIHandler(APIHandler):
    def __init__(self, conf):
        self.conf = conf
        if 'api_key' in conf:
            self.api_key = conf['api_key']
        else:
            self.api_key = os.environ['OPENAI_API_KEY']
        openai.api_key = self.api_key

    def complete(self, prompt):
        response = openai.Completion.create(
            request_timeout=120,
            model=self.conf['model'],
            prompt=prompt,
            temperature=float(self.conf['temperature']),
            max_tokens=int(self.conf['max_tokens']),
        )
        return response.choices[0].text.strip()

    def chat(self, messages):
        # for message in messages:
        #     print(message)
        response = openai.ChatCompletion.create(
            request_timeout=120,
            model=self.conf['model'],
            messages=messages,
            temperature=float(self.conf['temperature']),
            max_tokens=int(self.conf['max_tokens']),
        )
        return response['choices'][0]['message']['content']

