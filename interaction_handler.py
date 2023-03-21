import os
import json
import readline
import re
from datetime import datetime
from abc import ABC, abstractmethod

class InteractionHandler(ABC):
    @abstractmethod
    def start(self, message):
        pass

class FileCompletion(InteractionHandler):
    def __init__(self, session):
        self.api_handler = session['api_handler']

    def start(self, prompt):
        result = self.api_handler.complete(prompt)
        print(result)


class Completion(InteractionHandler):
    def __init__(self, session):
        self.api_handler = session['api_handler']

    def start(self, prompt):
        prompt += " User: "
        prompt += input("You: ")
        result = self.api_handler.complete(prompt)
        print(f"AI: " + result)



class Chat(InteractionHandler):
    def __init__(self, session):
        self.session = session
        self.api_handler = session['api_handler']

    def start(self, prompt):
        commands = ["save", "load", "quit", "exit", "help"]
        if 'load_chat' in self.session:
            messages = self.load_chat(self.session['load_chat'])
            if messages is not None:
                for message in messages[1:]:
                    print(f"{message['role'].capitalize()}: {message['content']}\n")
        else:
            messages = [{"role": "system", "content": prompt}]
        readline.set_completer(self.directory_completer)
        readline.parse_and_bind("tab: complete")
        while True:
            user_input = input("You: ")

            if user_input.strip() in commands:
                if user_input.strip() == "quit" or user_input.strip() == "exit":
                    break
                if user_input.strip() == "save":
                    default_filename = "chat_" + datetime.now().strftime("%Y-%m-%d_%s") + self.session['chats_extension']
                    filename = input(f"Enter a filename ({default_filename}): ")
                    if filename == "":
                        filename = default_filename
                    if not filename.endswith(self.session['chats_extension']):
                        filename += self.session['chats_extension']
                    self.save_chat(messages, filename)
                    continue
                if user_input.strip() == "load":
                    filename = input("Enter a filename: ")
                    loading = self.load_chat(filename)
                    if loading is not None:
                        messages = loading
                        # print messages skipping the first 'system' message
                        for message in messages[1:]:
                            print(f"{message['role'].capitalize()}: {message['content']}\n")

                    continue
                if user_input.strip() == "help":
                    print("Commands:")
                    print("save - save the chat history to a file")
                    print("load - load a chat history from a file")
                    print("quit - quit the chat")
                    continue

            messages.append({"role": "user", "content": user_input})


            response = self.api_handler.chat(messages)
            messages.append({"role": "assistant", "content": response})

            print("\nAI:", response, "\n")


    def save_chat(self, messages, filename):
        session_info = self.session
        if not os.path.isabs(filename):
            filename = os.path.join(session_info['chats_directory'], filename)
        contents = str()
        exclude = ['api_key', 'api_handler', 'chats_directory', 'prompt', 'load_chat', 'chats_extension']
        for parameter in session_info:
            if parameter not in exclude:
                contents += f"{parameter}: {session_info[parameter]}\n"
        contents += '_' * 80 + '\n'
        for message in messages:
            contents += f"{message['role']}: {message['content']}\n\n"
        with open(filename, "w") as f:
            f.write(contents)

    def load_chat(self, filename):
        if not os.path.isabs(filename):
            filename = os.path.join(self.session['chats_directory'], filename)
        if not os.path.exists(filename):
            print("File does not exist.")
            return
        print("Loading chat history...")
        with open(filename, "r") as f:
            contents = f.read()
            split_char = '_'
            min_char = 5
            message_section = re.split(rf'{split_char}{{{min_char},}}', contents)
            pattern = re.compile(r'(system|user|assistant):(.+?)(?=(?:system|user|assistant):|$)', re.DOTALL)
            message_parts = pattern.findall(message_section[1])
            messages = []
            for message in message_parts:
                messages.append({"role": message[0].strip(), "content": message[1].strip()})
            return messages

    def directory_completer(self, text, state):
        if not os.path.isabs(self.session['chats_directory']):
            chats_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.session['chats_directory'])
        else:
            chats_directory = self.session['chats_directory']
        files_and_dirs = os.listdir(chats_directory)
        options = [x for x in files_and_dirs if x.startswith(text)]
        try:
            return options[state]
        except IndexError:
            return None
