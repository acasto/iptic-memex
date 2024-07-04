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
            "load file": {
                "description": "Load a file into the context",
                "function": {"type": "action", "name": "load_file"},
            },
            "load web": {
                "description": "Load a web page into the context",
                "function": {"type": "action", "name": "fetch_from_web"},
            },
            "load search": {
                "description": "Search the web",
                "function": {"type": "action", "name": "brave_summary"},
            },
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
            "clear screen": {
                "description": "Clear the screen",
                "function": {"type": "action", "name": "clear_chat", "args": "screen"},
            },
            "cls": {
                "description": "Clear the screen",
                "function": {"type": "action", "name": "clear_chat", "args": "screen"},
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
            "load chat": {
                "description": "Load a saved chat",
                "function": {"type": "action", "name": "manage_chats", "args": "load"},
            },
            "list chats": {
                "description": "List saved chats",
                "function": {"type": "action", "name": "manage_chats", "args": "list"},
            },
            "export chat": {
                "description": "Export the current chat",
                "function": {"type": "action", "name": "manage_chats", "args": "export"},
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
        for command, details in self.commands.items():
            print(f"{command} - {details['description']}")
        print()

    def handle_quit(self):
        user_input = input("Hit Ctrl-C or enter 'y' to quit: ")
        if user_input.lower() == 'y':
            print()
            quit()
        else:
            print()
