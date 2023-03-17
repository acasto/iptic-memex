class Completion:
    def __init__(self, api_handler):
        self.api_handler = api_handler

    def start(self):
        prompt = input("Enter your prompt: ")
        result = self.api_handler.complete(prompt)
        print("Completion:", result)
