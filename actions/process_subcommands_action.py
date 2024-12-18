from session_handler import InteractionAction


class ProcessSubcommandsAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.get_action('tab_completion')
        self.chat = session.get_context('chat')
        self.commands = {
            "help": {
                "description": "Show available commands",
                "function": {"type": "method", "name": "handle_help"}
            },
            "quit": {
                "description": "Quit the chat",
                "function": {"type": "method", "name": "handle_quit"}
            },
            "exit": {
                "description": "Quit the chat",
                "function": {"type": "method", "name": "handle_quit"}
            },
            "load project": {
                "description": "Load a project",
                "function": {"type": "action", "name": "load_project"}
            },
            "load file": {
                "description": "Load a file into the context",
                "function": {"type": "action", "name": "load_file"},
            },
            "load pdf": {
                "description": "Load a pdf into the context",
                "function": {"type": "action", "name": "load_pdf"},
            },
            "load sheet": {
                "description": "Load an xlsx file into the context",
                "function": {"type": "action", "name": "load_sheet"},
            },
            "load doc": {
                "description": "Load a docx fie into the context",
                "function": {"type": "action", "name": "load_doc"},
            },
            "load raw": {
                "description": "Load raw text into the context",
                "function": {"type": "action", "name": "load_raw"},
            },
            "load code": {
                "description": "Load code into the context",
                "function": {"type": "action", "name": "fetch_code_snippet"},
            },
            "load multiline": {
                "description": "Load multiple lines of text into the context",
                "function": {"type": "action", "name": "load_multiline"},
            },
            "load web": {
                "description": "Load a web page into the context",
                "function": {"type": "action", "name": "fetch_from_web"},
            },
            "load search": {
                "description": "Search the web",
                "function": {"type": "action", "name": "brave_summary"},
            },
            # "load summary": {
            #     "description": "Load a summary from the web",
            #     "function": {"type": "action", "name": "brave_summary"},
            # },
            "clear context": {
                "description": "Clear item from context",
                "function": {"type": "action", "name": "clear_context"},
            },
            "clear chat": {
                "description": "Reset the conversation state",
                "function": {"type": "action", "name": "clear_chat", "args": "chat"},
            },
            "clear last": {
                "description": "Remove the last message (optional number of messages)",
                "function": {"type": "action", "name": "clear_chat", "args": "last"},
            },
            "clear first": {
                "description": "Remove the first message (optional number of messages)",
                "function": {"type": "action", "name": "clear_chat", "args": "first"},
            },
            "clear": {
                "description": "Clear the screen",
                "function": {"type": "action", "name": "clear_chat", "args": "screen"},
            },
            "reprint": {
                "description": "Reprint the conversation",
                "function": {"type": "action", "name": "reprint_chat"},
            },
            "show settings": {
                "description": "List all settings",
                "function": {"type": "action", "name": "show", "args": "settings"},
            },
            "show models": {
                "description": "List all models",
                "function": {"type": "action", "name": "show", "args": "models"},
            },
            "show messages": {
                "description": "List all messages",
                "function": {"type": "action", "name": "show", "args": "messages"},
            },
            "show usage": {
                "description": "Show usage statistics",
                "function": {"type": "action", "name": "show", "args": "usage"},
            },
            "set option": {
                "description": "Set an option",
                "function": {"type": "action", "name": "set_option"},
            },
            "save chat": {
                "description": "Save the current chat",
                "function": {"type": "action", "name": "manage_chats", "args": "save"},
            },
            "save last": {
                "description": "Save the last message",
                "function": {"type": "action", "name": "manage_chats", "args": ["save", False, "last"]},
            },
            "save full": {
                "description": "Save the full conversation and context",
                "function": {"type": "action", "name": "manage_chats", "args": ["save", "full"]},
            },
            "load chat": {
                "description": "Load a saved chat",
                "function": {"type": "action", "name": "manage_chats", "args": "load"},
            },
            "save code": {
                "description": "Save a code snippet",
                "function": {"type": "action", "name": "save_code"},
            },
            "list chats": {
                "description": "List saved chats",
                "function": {"type": "action", "name": "manage_chats", "args": "list"},
            },
            "export chat": {
                "description": "Export the current chat",
                "function": {"type": "action", "name": "manage_chats", "args": "export"},
            },
            "run code": {
                "description": "Run code from the chat",
                "function": {"type": "action", "name": "run_code"},
            },
            "run command": {
                "description": "Run a command",
                "function": {"type": "action", "name": "run_command"},
            },
            "run persist": {
                "description": "Run a persist command",
                "function": {"type": "action", "name": "persist"},
            },
        }

    def run(self, user_input: str = None) -> bool | None:
        if user_input is None or len(user_input.split()) > 4:  # limit command checking to messages with >4 words
            return

        # Sort commands by length (longest first) for substring matching
        sorted_commands = sorted(self.commands.keys(), key=len, reverse=True)

        for command in sorted_commands:
            if user_input.lower().startswith(command):
                user_args = user_input[len(command):].strip().split()
                command_info = self.commands[command]

                # Prepare the arguments
                predefined_args = command_info["function"].get("args", [])
                if isinstance(predefined_args, str):
                    predefined_args = [predefined_args]
                all_args = predefined_args + user_args

                if command_info["function"]["type"] == "action":
                    action = self.session.get_action(command_info["function"]["name"])
                    action.run(all_args)
                else:
                    method = getattr(self, command_info["function"]["name"])
                    method(*all_args)
                return True

        return None

    def get_commands(self) -> list[str]:
        return list(self.commands.keys())

    def handle_help(self):
        print("Commands:")
        # Find the longest command to determine column width
        max_command_length = max(len(command) for command in self.commands)

        # Sort commands alphabetically
        sorted_commands = sorted(self.commands.items())

        for command, details in sorted_commands:
            # Use f-string with padding to align columns
            print(f"{command:<{max_command_length + 2}} - {details['description']}")
        print()

    def handle_quit(self):
        ui = self.session.get_action('ui')
        user_input = input(ui.color_wrap("Hit Ctrl-C or enter 'y' to quit: ", "red"))
        if user_input.lower() == 'y':
            print()
            self.session.get_action('persist_stats').run()
            quit()
        else:
            print()
