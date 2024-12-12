import os
import sqlite3
from session_handler import InteractionAction


class PersistAction(InteractionAction):
    """
    Action for handling persistent storage via SQLite
    """
    def __init__(self, session):
        self.session = session
        self.db_path = os.path.expanduser(session.conf.get_option('DEFAULT', 'user_db'))
        self._ensure_db_exists()
        self.tc = session.get_action('tab_completion')

    def _ensure_db_exists(self):
        """Create database and tables if they don't exist"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS keyvalue (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def set(self, key: str, value: str) -> bool:
        """Store or update a key-value pair"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO keyvalue (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (key, value))
                conn.commit()
                return True
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return False

    def get(self, key: str) -> str | None:
        """Retrieve a value by key"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT value FROM keyvalue WHERE key = ?', (key,))
                result = cursor.fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None

    def delete(self, key: str) -> bool:
        """Delete a key-value pair"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM keyvalue WHERE key = ?', (key,))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return False

    def list_keys(self) -> list[str]:
        """List all stored keys"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT key FROM keyvalue ORDER BY key')
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return []

    def run(self, args=None):
        """
        Interactive CLI for the persist store
        """
        print("Persistence Store Interface (q to quit)")
        print("Commands: set <key> <value>, get <key>, delete <key>, list")
        print()

        self.tc.run("chat")  # Reset to chat completion mode
        while True:
            try:
                command = input("> ").strip()
                
                if command.lower() in ['q', 'quit', 'exit']:
                    break
                
                parts = command.split()
                if not parts:
                    continue

                if parts[0] == "set" and len(parts) >= 3:
                    key = parts[1]
                    value = " ".join(parts[2:])
                    if self.set(key, value):
                        print(f"Stored: {key} = {value}")
                    else:
                        print("Failed to store value")
                
                elif parts[0] == "get" and len(parts) == 2:
                    value = self.get(parts[1])
                    if value is not None:
                        print(f"{parts[1]} = {value}")
                    else:
                        print(f"No value found for key: {parts[1]}")
                
                elif parts[0] == "delete" and len(parts) == 2:
                    if self.delete(parts[1]):
                        print(f"Deleted key: {parts[1]}")
                    else:
                        print(f"Key not found: {parts[1]}")
                
                elif parts[0] == "list":
                    keys = self.list_keys()
                    if keys:
                        print("Stored keys:")
                        for key in keys:
                            print(f"  {key}")
                    else:
                        print("No stored keys found")
                
                else:
                    print("Invalid command or arguments")
                    print("Usage:")
                    print("  set <key> <value>")
                    print("  get <key>")
                    print("  delete <key>")
                    print("  list")
                print()

            except (KeyboardInterrupt, EOFError):
                print()
                break

        self.tc.run("chat")  # Reset to chat completion mode
        print()
