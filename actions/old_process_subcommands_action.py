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
            return self.get_commands()

        if user_input in self.get_commands():
            command = user_input.strip().lower()
            handler_method = getattr(self, f"handle_{command.replace(' ', '_')}", None)

            if handler_method:
                return handler_method()
            else:
                print(f"Unknown command: {user_input}\n")
        else:
            return True

    def get_commands(self):
        return [
            "test",
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

    def handle_quit(self):
        user_input = input("Hit Ctrl-C or enter 'y' to quit: ")
        if user_input == 'y':
            print()
            quit()
        else:
            print()
            return

    def handle_exit(self):
        return self.handle_quit()

    def handle_save(self):
        print("Not implemented yet")

    def handle_load_chat(self):
        print("Not implemented yet")

    def handle_load_file(self):
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

    def handle_list_models(self):
        for section, options in self.session.list_models().items():
            print(section)

    def handle_set_model(self):
        self.tc.run('model')
        while True:
            model = input(f"Enter model name (or q to exit): ")
            if model == 'q':
                self.tc.run('chat')  # set the completion back to chat mode
                break
            if model in self.session.list_models():
                self.session.set_option('model', model)
                self.tc.run('chat')
                break

    def handle_set_option(self):
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

    def handle_clear(self):
        self.chat.clear()

    def handle_show_messages(self):
        print(self.session.get_provider().get_messages())

    def handle_show_session(self):
        print(self.session.get_session_state())

    def handle_show_usage(self):
        print(self.session.get_provider().get_usage())

    def handle_count_tokens(self):
        print(self.session.get_action('count_tokens').run())

    def handle_help(self):
        self.print_help()

    def handle_question_mark(self):
        self.print_help()

    def print_help(self):
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
