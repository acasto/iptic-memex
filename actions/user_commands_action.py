from __future__ import annotations

from base_classes import InteractionAction


class UserCommandsAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
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
            # Unified under 'load file'
            "load raw": {
                "description": "Load raw text into the context",
                "function": {"type": "action", "name": "load_raw"},
            },
            "load rag": {
                "description": "Load a RAG context",
                "function": {"type": "action", "name": "load_rag"},
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
            "load mcp": {
                "description": "Connect to an MCP server (http|stdio)",
                "function": {"type": "action", "name": "mcp_connect"},
            },
            "load mcp demo": {
                "description": "Load a demo MCP server with sample tools",
                "function": {"type": "action", "name": "mcp_demo"},
            },
            "load mcp resource": {
                "description": "Fetch an MCP resource and add to context",
                "function": {"type": "action", "name": "mcp_fetch_resource"},
            },
            "discover mcp tools": {
                "description": "Discover tools exposed by an MCP server",
                "function": {"type": "action", "name": "mcp_discover"},
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
            "clear": {
                "description": "Clear the screen",
                "function": {"type": "action", "name": "clear_chat", "args": "screen"},
            },
            "reprint": {
                "description": "Reprint the conversation",
                "function": {"type": "action", "name": "reprint_chat"},
            },
            "reprint all": {
                "description": "Reprint the full conversation (filtered)",
                "function": {"type": "action", "name": "reprint_chat", "args": "all"},
            },
            "reprint raw": {
                "description": "Reprint the conversation skipping output filters",
                "function": {"type": "action", "name": "reprint_chat", "args": "raw"},
            },
            "reprint raw all": {
                "description": "Reprint the conversation skipping output filters",
                "function": {"type": "action", "name": "reprint_chat", "args": ["raw", "all"]},
            },
            "show settings": {
                "description": "List all settings",
                "function": {"type": "action", "name": "show", "args": "settings"},
            },
            "show settings tools": {
                "description": "List all settings for tools",
                "function": {"type": "action", "name": "show", "args": "tool-settings"},
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
            "show cost": {
                "description": "Show cost estimates",
                "function": {"type": "action", "name": "show", "args": "cost"},
            },
            "show contexts": {
                "description": "List all contexts",
                "function": {"type": "action", "name": "show", "args": "contexts"},
            },
            "show tools": {
                "description": "List all tools",
                "function": {"type": "action", "name": "show", "args": "tools"},
            },
            "set option": {
                "description": "Set an option",
                "function": {"type": "action", "name": "set_option"},
            },
            "set option tools": {
                "description": "Set a tool related option",
                "function": {"type": "action", "name": "set_option", "args": "tools"},
            },
            "set search": {
                "description": "Set the web search model",
                "function": {"type": "action", "name": "assistant_websearch_tool", "method": "set_search_model"}
            },
            "save chat": {
                "description": "Save the current chat",
                "function": {"type": "action", "name": "manage_chats", "args": "save"},
            },
            "save last": {
                "description": "Save the last message or last <n> messages",
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
            "show chats": {
                "description": "List saved chats",
                "function": {"type": "action", "name": "manage_chats", "args": "list"},
            },
            # Unified MCP command
            "mcp": {
                "description": "List app-side MCP servers",
                "function": {"type": "action", "name": "mcp"},
            },
            "mcp tools": {
                "description": "List app-side MCP tools per server",
                "function": {"type": "action", "name": "mcp", "args": "tools"},
            },
            "mcp resources": {
                "description": "List app-side MCP resources per server",
                "function": {"type": "action", "name": "mcp", "args": "resources"},
            },
            "mcp provider": {
                "description": "Show provider MCP pass-through configuration",
                "function": {"type": "action", "name": "mcp", "args": ["provider-mcp"]},
            },
            "mcp status": {
                "description": "Show overall MCP status (app + provider)",
                "function": {"type": "action", "name": "mcp", "args": "status"},
            },
            "register mcp tools": {
                "description": "Register discovered MCP tools for this session",
                "function": {"type": "action", "name": "mcp_register_tools"},
            },
            "unregister mcp tools": {
                "description": "Remove dynamic MCP tools (optionally with a pattern)",
                "function": {"type": "action", "name": "mcp_unregister_tools"},
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
            "rag update": {
                "description": "Update the RAG context",
                "function": {"type": "action", "name": "rag_update"},
            },
            "rag status": {
                "description": "Show RAG index status",
                "function": {"type": "action", "name": "rag_status"},
            },
        }
        # Check for and load user commands
        user_commands = self.session.get_action('register_user_commands')
        if user_commands:
            new_commands = user_commands.run()
            if isinstance(new_commands, dict) and new_commands:
                # Shallow per-command merge to allow partial overrides
                for name, cfg in new_commands.items():
                    if name in self.commands and isinstance(self.commands[name], dict) and isinstance(cfg, dict):
                        merged = dict(self.commands[name])
                        merged.update(cfg)
                        self.commands[name] = merged
                    else:
                        self.commands[name] = cfg
        # Filter out commands whose actions can't run
        for cmd_name, cmd_info in list(self.commands.items()):
            if cmd_info["function"]["type"] == "action":
                try:
                    action_name = cmd_info["function"]["name"]
                    class_name = ''.join(word.capitalize() for word in action_name.split('_')) + 'Action'
                    mod = __import__(f'actions.{action_name}_action', fromlist=[class_name])
                    action_class = getattr(mod, class_name)
                    if hasattr(action_class, 'can_run') and action_class.can_run(self.session) is False:
                        del self.commands[cmd_name]
                except ImportError:
                    pass

    def run(self, user_input: str = None) -> bool | None:
        if user_input is None or len(user_input.split()) > 4:  # limit command checking to messages with >4 words
            return None

        # Sort commands by length (longest first) for substring matching
        sorted_commands = sorted(self.commands.keys(), key=len, reverse=True)

        for command in sorted_commands:
            # Check if it's an exact match or if it's followed by a space
            if user_input.lower() == command or user_input.lower().startswith(command + " "):
                user_args = user_input[len(command):].strip().split()
                command_info = self.commands[command]

                # Prepare the arguments
                predefined_args = command_info["function"].get("args", [])
                if isinstance(predefined_args, str):
                    predefined_args = [predefined_args]
                all_args = predefined_args + user_args

                if command_info["function"]["type"] == "action":
                    action = self.session.get_action(command_info["function"]["name"])
                    if "method" in command_info["function"]:
                        method = getattr(action.__class__, command_info["function"]["method"])
                        method(self.session, *all_args if all_args else [])
                    else:
                        action.run(all_args)
                else:
                    method = getattr(self, command_info["function"]["name"])
                    method(*all_args)
                return True

        return None

    def get_commands(self) -> list[str]:
        return list(self.commands.keys())

    def handle_help(self):
        if not getattr(self.session.ui.capabilities, 'blocking', False):
            lines = ["Commands:"]
            for command, details in sorted(commands.items()):
                lines.append(f"{command} - {details['description']}")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass
            return
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
        if self.session.handle_exit():
            quit()
