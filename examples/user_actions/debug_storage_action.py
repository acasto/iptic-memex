from base_classes import InteractionAction


class DebugStorageAction(InteractionAction):
    """
    Debug interface for examining and manipulating stored data
    """
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.current_table = "keyvalue"  # Default to the main KV table

    @staticmethod
    def print_help():
        """Print available commands"""
        commands = {
            "Table Selection": {
                "use <table>": "Select a table for row operations",
                "current": "Show currently selected table",
            },
            "Key-Value Operations (keyvalue table only)": {
                "set <key> <value>": "Store a key-value pair",
                "get <key>": "Retrieve a value by key",
                "delete <key>": "Delete a key-value pair",
                "list keys": "List all stored keys",
            },
            "Row Operations (any table)": {
                "list": "List all rows in current table",
                "find <column> <value>": "Find rows where column equals value",
                "add <col1=val1> <col2=val2> ...": "Add a row with specified values",
                "remove <id>": "Remove row by ID (first column)",
                "schema": "Show table structure",
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
                print(f"  {cmd:<30} - {desc}")
        print()

    def handle_table_selection(self, parts):
        """Handle table selection commands"""
        if parts[0] == "use" and len(parts) == 2:
            table = parts[1]
            # Check if table exists
            provider = self.session.utils.storage.provider
            result = provider.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if result:
                self.current_table = table
                print(f"Now using table: {table}")
                
                # Show table structure
                cols = provider.execute(f"PRAGMA table_info({table})")
                if cols:
                    print("Table structure:")
                    for col in cols:
                        pk = " (PRIMARY KEY)" if col[5] else ""
                        null = " NOT NULL" if col[3] else ""
                        default = f" DEFAULT {col[4]}" if col[4] else ""
                        print(f"  {col[1]} ({col[2]}){pk}{null}{default}")
            else:
                print(f"Table '{table}' not found")
                
        elif parts[0] == "current":
            print(f"Current table: {self.current_table}")
            # Show row count
            provider = self.session.utils.storage.provider
            try:
                result = provider.execute(f"SELECT COUNT(*) FROM {self.current_table}")
                if result:
                    print(f"Rows: {result[0][0]}")
            except Exception as e:
                print(f"Error getting row count: {e}")

    def handle_table_command(self, parts):
        """Handle table-related commands"""
        storage = self.session.utils.storage
        provider = storage.provider

        if parts[0] == "list" and parts[1] == "tables":
            result = provider.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if result:
                print("\nAvailable tables:")
                for row in result:
                    count_result = provider.execute(f"SELECT COUNT(*) FROM {row[0]}")
                    count = count_result[0][0] if count_result else 0
                    current_marker = " (current)" if row[0] == self.current_table else ""
                    print(f"  {row[0]} ({count} rows){current_marker}")
            else:
                print("No tables found")

        elif parts[0] == "show" and len(parts) == 2:
            table = parts[1]
            self._display_table_contents(table)

        elif parts[0] == "clear" and len(parts) == 2:
            table = parts[1]
            if storage.reset_table(table):
                print(f"Table '{table}' cleared")
            else:
                print(f"Failed to clear table '{table}'")

        elif parts[0] == "count" and len(parts) == 2:
            table = parts[1]
            try:
                result = provider.execute(f"SELECT COUNT(*) FROM {table}")
                if result:
                    print(f"Table '{table}' has {result[0][0]} rows")
                else:
                    print(f"Table '{table}' not found")
            except Exception as e:
                print(f"Error counting rows in '{table}': {e}")

    def handle_kv_command(self, parts):
        """Handle key-value store commands (only for keyvalue table)"""
        if self.current_table != "keyvalue":
            print("Key-value operations only work on the 'keyvalue' table. Use 'use keyvalue' first.")
            return

        storage = self.session.utils.storage
        
        if parts[0] == "set" and len(parts) >= 3:
            key = parts[1]
            value = " ".join(parts[2:])
            if storage.set(key, value):
                print(f"Stored: {key} = {value}")
            else:
                print("Failed to store value")

        elif parts[0] == "get" and len(parts) == 2:
            key = parts[1]
            value = storage.get(key)
            if value is not None:
                print(f"{key} = {value}")
            else:
                print(f"No value found for key: {key}")

        elif parts[0] == "delete" and len(parts) == 2:
            key = parts[1]
            if storage.delete(key):
                print(f"Deleted key: {key}")
            else:
                print(f"Key not found: {key}")

        elif parts[0] == "list" and len(parts) > 1 and parts[1] == "keys":
            keys = storage.list_keys()
            if keys:
                print(f"\nStored keys in {self.current_table}:")
                for key in keys:
                    value = storage.get(key)
                    # Truncate long values for display
                    display_value = value if len(value) <= 50 else value[:47] + "..."
                    print(f"  {key} = {display_value}")
            else:
                print("No stored keys found")

    def handle_row_command(self, parts):
        """Handle row operations for the current table"""
        provider = self.session.utils.storage.provider
        
        try:
            # Get table structure
            cols = provider.execute(f"PRAGMA table_info({self.current_table})")
            if not cols:
                print(f"Table '{self.current_table}' not found")
                return
            
            column_names = [col[1] for col in cols]
            
            if parts[0] == "list":
                self._display_table_contents(self.current_table)
                
            elif parts[0] == "schema":
                print(f"\nTable '{self.current_table}' structure:")
                for col in cols:
                    pk = " (PRIMARY KEY)" if col[5] else ""
                    null = " NOT NULL" if col[3] else ""
                    default = f" DEFAULT {col[4]}" if col[4] else ""
                    print(f"  {col[1]} ({col[2]}){pk}{null}{default}")
                    
            elif parts[0] == "find" and len(parts) >= 3:
                column = parts[1]
                value = " ".join(parts[2:])
                
                if column not in column_names:
                    print(f"Column '{column}' not found. Available columns: {', '.join(column_names)}")
                    return
                    
                result = provider.execute(f"SELECT * FROM {self.current_table} WHERE {column} = ?", (value,))
                if result:
                    print(f"\nFound {len(result)} rows where {column} = {value}:")
                    self._display_rows(result, column_names)
                else:
                    print(f"No rows found where {column} = {value}")
                    
            elif parts[0] == "add" and len(parts) >= 2:
                # Parse col=val pairs
                assignments = {}
                for part in parts[1:]:
                    if '=' in part:
                        col, val = part.split('=', 1)
                        col = col.strip()
                        val = val.strip()
                        if col in column_names:
                            assignments[col] = val
                        else:
                            print(f"Warning: Column '{col}' not found, skipping")
                
                if assignments:
                    cols_str = ', '.join(assignments.keys())
                    placeholders = ', '.join(['?' for _ in assignments])
                    values = tuple(assignments.values())
                    
                    provider.execute(f"INSERT INTO {self.current_table} ({cols_str}) VALUES ({placeholders})", values)
                    print(f"Added row to {self.current_table}")
                else:
                    print("No valid column assignments found. Use format: add col1=val1 col2=val2")
                    
            elif parts[0] == "remove" and len(parts) == 2:
                row_id = parts[1]
                first_col = column_names[0]  # Use first column as ID
                
                # Check existence before delete, since provider uses a new connection per call
                exists = provider.execute(f"SELECT 1 FROM {self.current_table} WHERE {first_col} = ? LIMIT 1", (row_id,))
                if exists:
                    provider.execute(f"DELETE FROM {self.current_table} WHERE {first_col} = ?", (row_id,))
                    print(f"Removed row where {first_col} = {row_id}")
                else:
                    print(f"No row found where {first_col} = {row_id}")
                    
        except Exception as e:
            print(f"Error performing row operation: {e}")

    def _display_table_contents(self, table):
        """Display formatted table contents"""
        provider = self.session.utils.storage.provider
        
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

        self._display_rows(rows, headers)

    def _display_rows(self, rows, headers):
        """Display rows in a formatted table"""
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

    def run(self, args=None):
        """Interactive CLI for storage inspection and manipulation (CLI-only)."""
        if not getattr(self.session.ui.capabilities, 'blocking', False):
            try:
                self.session.ui.emit('warning', {'message': 'DebugStorageAction is only available in the CLI mode.'})
            except Exception:
                pass
            return
        print(f"Storage Debug Interface (q to quit, help for commands)")
        print(f"Current table: {self.current_table}")
        self.tc.run("chat")  # Reset to chat completion mode

        while True:
            try:
                command = input(f"\n[{self.current_table}]> ").strip()

                if command.lower() in ['q', 'quit', 'exit']:
                    break

                if not command:
                    continue

                if command == "help":
                    self.print_help()
                    continue

                parts = command.split()

                # Table selection commands
                if parts[0] in ["use", "current"]:
                    self.handle_table_selection(parts)
                # Table operations
                elif parts[0] in ["list", "show", "clear", "count"] and len(parts) > 1:
                    self.handle_table_command(parts)
                # Key-value operations (keyvalue table only)
                elif parts[0] in ["set", "get", "delete"] or (parts[0] == "list" and len(parts) > 1 and parts[1] == "keys"):
                    self.handle_kv_command(parts)
                # Row operations (any table)
                elif parts[0] in ["list", "find", "add", "remove", "schema"]:
                    self.handle_row_command(parts)
                else:
                    print("Invalid command. Type 'help' for available commands.")

            except (KeyboardInterrupt, EOFError):
                print()
                break
            except Exception as e:
                print(f"Error: {str(e)}")

        self.tc.run("chat")  # Reset to chat completion mode
        print()
