import os
from session_handler import InteractionAction


class ProcessSubcommandsAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.get_action('tab_completion')
        self.chat = session.get_context('chat')
        self.commands = {
            "quit": [],
            "exit": [],
            "load": ["file"],
            "save": [],
            "show": ["models", "messages", "settings", "usage"],
            "set": ["model", "option"],
            "clear": [],
            "help": [],
            "run": ["count_tokens"],
            "?": [],
        }

    def run(self, user_input: str = None) -> bool | None:
        """
        Process subcommands from the chat mode
        user_input: str - the user input to process
        bool | None - True if we should continue, None if we should keep going with the response
        """
        if user_input is None:
            return

        words = user_input.strip().lower().split()
        if len(words) > 4 or len(words) == 0 :  # Ignore inputs with more than 4 words or no words
            return

        command, *rest = words
        if command not in self.commands:  # If first word isn't a command, return
            return

        if not self.commands[command]:  # No subcommands, call handler directly (e.g. quit, help, etc.)
            handler_method = getattr(self, f"handle_{command}")
            handler_method()
            print()
            return True

        if len(words) == 2:  # If two words, check if the second word is a subcommand, else assume it's an arg
            subcommand = words[1]
            if subcommand in self.commands[command]:
                handler_method = getattr(self, f"handle_{command.replace(' ', '_')}_{subcommand.replace(' ', '_')}")
                handler_method()
            else:
                getattr(self, f"handle_{command}")(rest)
            print()
            return True

        if len(words) > 2:  # More than two words, assume they're args for the subcommand
            getattr(self, f"handle_{command}_{words[1]}")(words[2:])
            print()
            return True

        return False

    def get_commands(self) -> list[str]:
        # flatten the commands dict but omit the keys with values (e.g. "load model", "set option")
        return [f"{k} {v}" if values else k for k, values in self.commands.items() for v in (values if values else [None])]

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

    def handle_load_file(self, args=None):
        if not args:
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
            return

        filename = ' '.join(args)
        if os.path.isfile(filename):
            self.session.add_context('file', filename)
            self.tc.run('chat')  # set the completion back to chat mode
        else:
            print(f"File {filename} not found.")

    def handle_show_models(self):
        for section, options in self.session.list_models().items():
            print(section)

    def handle_set_model(self, args=None):
        if not args:
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
            return

        model_name = ' '.join(args)
        if model_name in self.session.list_models():
            self.session.set_option('model', model_name)
            self.tc.run('chat')  # set the completion back to chat mode
        else:
            print(f"Model {model_name} not found.")

    def handle_set_option(self, args=None):
        if args is None:
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
            return

        if len(args) == 1:  # No value provided
            print(f"Usage: set option {args[0]} <value>")
            return

        option, *value = args
        if option in self.session.get_params():
            value = ' '.join(value) if value else ''  # Join remaining args into a single value
            self.session.set_option(option, value)
            print(f"Option {option} set to {value}")
        else:
            print(f"Option {option} not found.")

    def handle_clear(self):
        self.chat.clear()

    def handle_show_messages(self):
        print(self.session.get_provider().get_messages())

    def handle_show_settings(self):
        print(self.session.get_session_state())

    def handle_show_usage(self):
        print(self.session.get_provider().get_usage())

    def handle_run_count_tokens(self):
        self.session.get_action('count_tokens').run()

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
