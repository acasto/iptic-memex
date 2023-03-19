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
        if 'api_key' in conf['OPENAI']:
            self.api_key = conf['OPENAI']['api_key']
        else:
            self.api_key = os.environ['OPENAI_API_KEY']
        openai.api_key = self.api_key

    def complete(self, prompt):
        response = openai.Completion.create(
            engine=self.conf['OPENAI']['api_completion_model'],
            prompt=prompt,
            temperature=float(self.conf['OPENAI']['api_temperature']),
            max_tokens=int(self.conf['OPENAI']['api_max_tokens']),
        )
        return response.choices[0].text.strip()

    def chat(self, message):
        response = openai.Completion.create(
            engine=self.conf['OPENAI']['api_chat_model'],
            prompt=f"{message}\nAI:",
            temperature=self.conf['OPENAI']['api_temperature'],
            max_tokens=self.conf['OPENAI']['api_max_tokens'],
        )
        return response.choices[0].text.strip()

    def set_option(self, section, option, value):
        self.conf[section][option] = value

    def get_option(self, section, option):
        return self.conf[section][option]



