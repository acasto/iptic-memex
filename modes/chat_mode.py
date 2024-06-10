import os
import sys
import time
from session_handler import InteractionMode
from pathlib import Path


class ChatMode(InteractionMode):
    """
    Interaction handler for chat mode
    """

    def __init__(self, session):
        self.session = session
        self.conf = session.get_session_settings()
        self.provider = session.get_provider()

        # Initialize a chat context object
        session.add_context('chat')  # initialize a chat context object
        self.chat = self.conf['loadctx']['chat'][0]  # get the chat context object

        # User commands we want to support
        self.commands = ["save", "load", "clear", "quit", "exit", "help", "?", "show messages", "show tokens"]

    def start(self):
        # Get the labels
        user_label = self.session.get_label('user')
        response_label = self.session.get_label('response')

        # Start the chat session loop
        while True:

            # Get contexts that have been loaded into loadctx
            contexts = []
            if 'file' in self.conf['loadctx']:  # todo: we'll need to revisit this with additional contexts
                contexts.extend(self.conf['loadctx']['file'])
                self.conf['loadctx'].pop('file')  # remove the file from loadctx

            # Let the user know what file(s) we are working with
            if len(contexts) > 0:
                print()
                for context in contexts:
                    print(f"In context: {context.get()['name']}")
                print()

            # Get the users input
            self.activate_completion('chat')
            user_input = input(f"{user_label} ")

            # Check for user commands
            if user_input.strip() in self.commands:
                if user_input.strip() == "quit" or user_input.strip() == "exit":
                    break
            if user_input.strip() == "save":
                print("Not implemented yet")
                continue
            if user_input.strip() == "load chat":
                print("Not implemented yet")
                continue
            if user_input.strip() == "load file":
                self.activate_completion('path')
                while True:
                    filename = input(f"Enter a filename: ")
                    if os.path.isfile(filename):
                        self.session.add_context('file', filename)
                        self.deactivate_completion()
                        break
                    else:
                        print(f"File {filename} not found.")
                continue
            if user_input.strip() == "list models":
                for section, options in self.session.list_models().items():
                    print(section)
                continue
            if user_input.strip() == "set model":
                self.activate_completion('model')
                while True:
                    model = input(f"Enter a model: ")
                    if self.session.conf.valid_model(model):
                        self.provider = self.session.get_provider(model)
                    else:
                        print(f"Model {model} not found.")
            if user_input.strip() == "clear":
                print("Not implemented yet")
                continue
            if user_input.strip() == "show messages":
                print("Not implemented yet")
                continue
            if user_input.strip() == "show tokens":
                print("Not implemented yet")
                continue
            if user_input.strip() == "help" or user_input.strip() == "?":
                print("Commands:")
                print("save - save the chat history to a file")
                print("load chat - load a chat history from a file")
                print("load file - load a file into the context")
                print("clear - clear the context and start over")
                print("show messages - dump session messages")
                print("show tokens - show number of tokens in session")
                print("quit - quit the chat")
                continue
            # end user commands

            # Add the question to the chat context
            self.chat.add(user_input, 'user', contexts)
            del contexts  # clear contexts now that we have added them to the chat

            # Start the response
            print(f"{response_label} ", end='', flush=True)
            # if we are in stream mode, iterate through the stream of events
            if self.conf['parms']['stream'] is True:
                accumulator = ''
                response = self.provider.stream_chat(self.conf['loadctx'])
                # iterate through the stream of events, add in a delay to simulate a more natural conversation
                if response:
                    for i, event in enumerate(response):
                        print(event, end='', flush=True)
                        accumulator += event  # accumulate the response so we can save it.
                        if 'stream_delay' in self.conf['parms']:
                            time.sleep(float(self.conf['parms']['stream_delay']))
                print()
                self.chat.add(accumulator, 'assistant')

            # else just print the response
            else:
                response = self.provider.chat(self.conf['loadctx'])
                print(response)
                self.chat.add(response, 'assistant')

            print()
            # activity = self.provider.get_usage()
            # if activity:
            #     print()
            #     print(f"Tokens: {activity}")
            #     print()

    ###############################################################
    #  Utility Methods
    ###############################################################

    def process_subcommands(self, user_input):
        pass

    def activate_completion(self, completer="path"):
        """
        Enables tab completion, defaults to file path completion
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.set_completer_delims('\t\n')
            # set the completer to completer if it exists
            readline.set_completer(getattr(self, f"{completer}_completer"))
            readline.parse_and_bind("tab: complete")

    @staticmethod
    def deactivate_completion():
        """
        Disables tab completion by making tab insert a tab
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.parse_and_bind('tab: self-insert')

    def chat_completer(self, text, state):
        options = self.commands
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    @staticmethod
    def path_completer(text, state):
        """
        Enables tab completion for file paths
        """
        if text.startswith('~'):  # if text begins with '~' expand it
            text = os.path.expanduser(text)
        if os.path.isdir(os.path.dirname(text)):
            files_and_dirs = [str(Path(os.path.dirname(text)) / x) for x in os.listdir(os.path.dirname(text))]
        else:  # will catch CWD and empty inputs
            files_and_dirs = os.listdir(os.getcwd())

        # find the options that match
        options = [x for x in files_and_dirs if x.startswith(text)]

        # return the option at the current state
        try:
            # print(f"Text: {text!r}, State: {state}, Options: {options!r}")
            return options[state]
        except IndexError:
            return None

    def model_completer(self, text, state):
        """
        Enables tab completion for active modles
        """
        # build a list from the keys in list_models()
        options = [x for x in self.session.list_models().keys()]
        # if an element in options starts with text, return it
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None
