import os
import json

class Chat:
    def __init__(self, api_handler):
        self.api_handler = api_handler
        self.history = []

    def start(self):
        while True:
            message = input("You: ")
            if message.lower() == "exit":
                break
            response = self.api_handler.chat(message)
            self.history.append((message, response))
            print("Bot:", response)

    def save(self, filename):
        with open(filename, "w") as f:
            json.dump(self.history, f)

    def load(self, filename):
        if os.path.exists(filename):
            with open(filename, "r") as f:
                self.history = json.load(f)
