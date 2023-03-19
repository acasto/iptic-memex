# from api_handler import OpenAIHandler

class Completion:
    def __init__(self, api_handler):
        self.api_handler = api_handler
        self.conf = api_handler.conf

    def start(self):
        # print(self.api_handler)
        # if isinstance(self.api_handler, OpenAIHandler):
        #     print("OpenAIHandler")
        prompt = input("Enter your prompt: ")
        result = self.api_handler.complete(prompt)
        print("Completion:", result)
