import os
from session_handler import InteractionAction


class ProcessSubcommandsAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.get_action('tab_completion')
        self.chat = session.get_context('chat')

    def run(self, user_input=None):
        """
        Process subcommands from the chat mode
        """
        if user_input is None:
            return [
                "quit",
                "exit",
                "save",
                "load chat",
                "load file",
                "list models",
                "set model",
                "set option",
                "clear",
                "show messages",
                "show session",
                "show usage",
                "count tokens",
                "help",
                "?"
            ]
        if user_input.strip() == "quit" or user_input.strip() == "exit":
            user_input = input("Hit Ctrl-C or enter 'y' to quit: ")
            if user_input == 'y':
                print()
                quit()
            else:
                print()
                return
        if user_input.strip() == "save":
            print("Not implemented yet")
        if user_input.strip() == "load chat":
            print("Not implemented yet")
        if user_input.strip() == "load file":
            self.tc.run('path')
            while True:
                filename = input(f"Enter filename (or q to exit): ")
                if filename == 'q':
                    self.tc.run('chat')
                    break
                if os.path.isfile(filename):
                    self.session.add_context('file', filename)
                    self.tc.run('chat')  # set the completion back to chat mode
                    break
                else:
                    print(f"File {filename} not found.")
        if user_input.strip() == "list models":
            for section, options in self.session.list_models().items():
                print(section)
        if user_input.strip() == "set model":
            self.tc.run('model')
            while True:
                model = input(f"Enter model name (or q to exit): ")
                if model == 'q':
                    self.tc.run('chat')  # the completion back to chat mode
                    break
                if model in self.session.list_models():
                    self.session.set_option('model', model)
                    self.tc.run('chat')
                    break
        if user_input.strip() == "set option":
            self.tc.run('option')
            while True:
                option = input(f"Enter option name (or q to exit): ")
                if option == 'q':
                    self.tc.run('chat')
                    break
                if option in self.session.get_params():
                    value = input(f"Enter value for {option}: ")
                    self.session.set_option(option, value)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Option {option} not found.")
            pass
        if user_input.strip() == "clear":
            self.chat.clear()
        if user_input.strip() == "show messages":
            print(self.session.get_provider().get_messages())
        if user_input.strip() == "show session":
            print(self.session.get_session_state())
        if user_input.strip() == "show usage":
            print(self.session.get_provider().get_usage())
        if user_input.strip() == "count tokens":
            print(self.session.get_action('count_tokens').run())
        if user_input.strip() == "help" or user_input.strip() == "?":
            print("Commands:")
            print("save - save the chat history to a file")
            print("load chat - load a chat history from a file")
            print("load file - load a file into the context")
            print("list models - list available models")
            print("load model - switch to a different model")
            print("clear - clear the context and start over")
            print("show messages - dump session messages")
            print("show tokens - show number of tokens in session")
            print("quit - quit the chat")
        return
