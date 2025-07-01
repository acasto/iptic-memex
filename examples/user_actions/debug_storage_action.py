from session_handler import InteractionAction


class DebugStorageAction(InteractionAction):
    """
    Debug interface for examining and manipulating stored data
    """
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    @staticmethod
    def print_help():
        """Print available commands"""
        commands = {
            "Key-Value Operations": {
                "set <key> <value>": "Store a key-value pair",
                "get <key>": "Retrieve a value by key",
                "delete <key>": "Delete a key-value pair",
                "list keys": "List all stored keys",
            },
            "Table Operations": {
                "list tables": "Show all tables in database",
                "show <table>": "Display contents of a table",
                "clear <table>": "Clear all data from a table",
                "count <table>": "Show row count for a table",
            },
            "Other Commands": {
                "help": "Show this help message",
                "q/quit/exit": "Exit debug interface",
            }
        }

        print("\nStorage Debug Commands:")
        for category, cmds in commands.items():
            print(f"\n{category}:")
            for cmd, desc in cmds.items():
                print(f"  {cmd:<20} - {desc}")
        print()

    def handle_table_command(self, parts):
        """Handle table-related commands"""
        storage = self.session.utils.storage
        provider = storage.provider

        if parts[0] == "list" and parts[1] == "tables":
            result = provider.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if result:
                print("\nAvailable tables:")
                for row in result:
                    count = provider.execute(f"SELECT COUNT(*) FROM {row[0]}")[0][0]
                    print(f"  {row[0]} ({count} rows)")
            else:
                print("No tables found")

        elif parts[0] == "show" and len(parts) == 2:
            table = parts[1]
            # Get column names
            cols = provider.execute(f"PRAGMA table_info({table})")
            if not cols:
                print(f"Table '{table}' not found")
                return

            headers = [col[1] for col in cols]
            rows = provider.execute(f"SELECT * FROM {table}")

            if not rows:
                print(f"No data in table '{table}'")
                return

            # Calculate column widths
            widths = [len(h) for h in headers]
            for row in rows:
                for i, val in enumerate(row):
                    widths[i] = max(widths[i], len(str(val)))

            # Print headers
            header_line = " | ".join(f"{h:<{w}}" for h, w in zip(headers, widths))
            print("\n" + header_line)
            print("-" * len(header_line))

            # Print rows
            for row in rows:
                print(" | ".join(f"{str(val):<{w}}" for val, w in zip(row, widths)))
            print()

        elif parts[0] == "clear" and len(parts) == 2:
            if storage.reset_table(parts[1]):
                print(f"Table '{parts[1]}' cleared")
            else:
                print(f"Failed to clear table '{parts[1]}'")

        elif parts[0] == "count" and len(parts) == 2:
            result = provider.execute(f"SELECT COUNT(*) FROM {parts[1]}")
            if result:
                print(f"Table '{parts[1]}' has {result[0][0]} rows")
            else:
                print(f"Table '{parts[1]}' not found")

    def handle_kv_command(self, parts):
        """Handle key-value store commands"""
        storage = self.session.utils.storage

        if parts[0] == "set" and len(parts) >= 3:
            key = parts[1]
            value = " ".join(parts[2:])
            if storage.set(key, value):
                print(f"Stored: {key} = {value}")
            else:
                print("Failed to store value")

        elif parts[0] == "get" and len(parts) == 2:
            value = storage.get(parts[1])
            if value is not None:
                print(f"{parts[1]} = {value}")
            else:
                print(f"No value found for key: {parts[1]}")

        elif parts[0] == "delete" and len(parts) == 2:
            if storage.delete(parts[1]):
                print(f"Deleted key: {parts[1]}")
            else:
                print(f"Key not found: {parts[1]}")

        elif parts[0] == "list" and parts[1] == "keys":
            keys = storage.list_keys()
            if keys:
                print("\nStored keys:")
                for key in keys:
                    value = storage.get(key)
                    print(f"  {key} = {value}")
            else:
                print("No stored keys found")

    def run(self, args=None):
        """Interactive CLI for storage inspection and manipulation"""
        print("Storage Debug Interface (q to quit, help for commands)")
        self.tc.run("chat")  # Reset to chat completion mode

        while True:
            try:
                command = input("\n> ").strip()

                if command.lower() in ['q', 'quit', 'exit']:
                    break

                if not command:
                    continue

                if command == "help":
                    self.print_help()
                    continue

                parts = command.split()

                # Table operations
                if parts[0] in ["list", "show", "clear", "count"]:
                    self.handle_table_command(parts)
                # Key-value operations
                elif parts[0] in ["set", "get", "delete"] or (parts[0] == "list" and len(parts) > 1 and parts[1] == "keys"):
                    self.handle_kv_command(parts)
                else:
                    print("Invalid command. Type 'help' for available commands.")

            except (KeyboardInterrupt, EOFError):
                print()
                break
            except Exception as e:
                print(f"Error: {str(e)}")

        self.tc.run("chat")  # Reset to chat completion mode
        print()
