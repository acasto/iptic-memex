from session_handler import InteractionAction
import subprocess


class AssistantCmdToolAction(InteractionAction):
    """
    Action for handling cmd operations
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    def run(self, args: dict, content: str = ""):
        # A dictionary of allowed commands mapped to their handler functions.
        # Each handler function takes a list of arguments and returns (success, output).
        safe_dispatch = {
            'date': self.run_date,
            'ls': self.run_ls,
            'pwd': self.run_pwd,
            'cat': self.run_cat,
            'grep': self.run_grep,
            'find': self.run_find,
            'head': self.run_head,
            'tail': self.run_tail,
            # Add more handlers here if needed.
        }

        # Retrieve requested command and arguments (if any)
        command = args.get('command', '').strip()
        raw_args = args.get('arguments', '').strip()

        # Basic sanity checks:
        # Strip out shell metacharacters from arguments to reduce injection risks.
        # This is a simple safeguard; more robust solutions might be needed in production.
        dangerous_chars = [';', '|', '&', '`', '$', '<', '>', '(', ')', '{', '}', '!', '\\']
        for c in dangerous_chars:
            raw_args = raw_args.replace(c, '')

        # Split arguments on whitespace for safer handling
        arg_list = raw_args.split()

        if command not in safe_dispatch:
            allowed = ', '.join(safe_dispatch.keys())
            msg = f"Command '{command}' is not allowed. Allowed commands: {allowed}"
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': msg})
            return

        # Call the appropriate handler function
        success, output = safe_dispatch[command](arg_list, content)
        if success:
            self.session.add_context('assistant', {'name': f'{command} command', 'content': output})
        else:
            self.session.add_context('assistant', {'name': f'{command} command', 'content': f"Error: {output}"})

    @staticmethod
    def run_date(arg_list, content):
        # date typically takes no arguments safely
        if len(arg_list) == 0:
            output = subprocess.run(['date'], capture_output=True, text=True)
            return True, output.stdout if output.returncode == 0 else output.stderr
        else:
            return False, f"date does not support arguments."

    @staticmethod
    def run_pwd(arg_list, content):
        # pwd typically takes no arguments safely
        if len(arg_list) == 0:
            output = subprocess.run(['pwd'], capture_output=True, text=True)
            return True, output.stdout if output.returncode == 0 else output.stderr
        else:
            return False, f"pwd does not support arguments."

    @staticmethod
    def run_ls(arg_list, content):
        # Separate options (start with '-') from positional arguments (paths)
        options = [a for a in arg_list if a.startswith('-')]
        paths = [a for a in arg_list if not a.startswith('-')]

        # If no paths are given, default to '.'
        if not paths:
            paths = ['.']

        # Validate that no path is absolute
        # for p in paths:
        #     if p.startswith('/'):
        #         return False, f"Absolute paths not allowed: {p}"

        # Construct the final command
        cmd = ['ls'] + options + paths
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)

    @staticmethod
    def run_grep(arg_list, content):
        # grep usage: grep [options] pattern file
        # Let's find the last argument as file (path), the first non-option as pattern.
        options = [a for a in arg_list if a.startswith('-')]
        non_options = [a for a in arg_list if not a.startswith('-')]

        # We need at least a pattern and a file
        if len(non_options) < 2:
            return False, "Please provide a pattern and a file for grep. Usage: grep [options] pattern file"

        # The last non-option argument should be the file
        file_path = non_options[-1]
        pattern = ' '.join(non_options[:-1])  # If pattern is multiple words, join them.
        # If pattern is strictly one argument, just use non_options[-2] before file.
        # But let's assume multi-word patterns are allowed.

        if file_path.startswith('/'):
            return False, f"Absolute paths are not allowed in grep: {file_path}"

        cmd = ['grep'] + options + [pattern, file_path]
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)

    @staticmethod
    def run_find(arg_list, content):
        # find usage: find [path] [options...]
        # Usually the first argument after 'find' is the path, then options/conditions follow.
        if not arg_list:
            # Default path is '.' if none provided
            arg_list = ['.']

        path = arg_list[0]
        # if path.startswith('/'):
        #     return False, f"Absolute paths are not allowed in find: {path}"

        # The rest are options/conditions
        conditions = arg_list[1:]

        # Disallow '-exec' for safety
        if '-exec' in conditions:
            return False, "The -exec option is not allowed due to security concerns."

        # For simplicity, let's just allow conditions that do not contain absolute paths.
        # Patterns like '-name test.py' are allowed. Just check that no argument starts with '/'
        for c in conditions:
            if c.startswith('/'):
                return False, f"Argument '{c}' is not allowed as it starts with '/'."

        # Construct the final command
        cmd = ['find', path] + conditions
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)

    @staticmethod
    def run_cat(arg_list, content):
        # Separate options from file arguments
        options = [a for a in arg_list if a.startswith('-')]
        files = [a for a in arg_list if not a.startswith('-')]

        # Validate that no file path is absolute
        # for f in files:
        #     if f.startswith('/'):
        #         return False, f"Absolute paths are not allowed in cat: {f}"

        # If no files are provided, cat reads from stdin - this is allowed.
        # Construct the command
        cmd = ['cat'] + options + files
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)

    @staticmethod
    def run_head(arg_list, content):
        # Separate options from files
        # Example usage: head -n 10 file.txt
        options = [a for a in arg_list if a.startswith('-')]
        files = [a for a in arg_list if not a.startswith('-')]

        # Validate that no file path is absolute
        # for f in files:
        #     if f.startswith('/'):
        #         return False, f"Absolute paths not allowed in head: {f}"

        # If no files are given, head will read from stdin.
        cmd = ['head'] + options + files
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)

    @staticmethod
    def run_tail(arg_list, content):
        # Separate options from files
        # Example usage: tail -n 10 file.txt
        options = [a for a in arg_list if a.startswith('-')]
        files = [a for a in arg_list if not a.startswith('-')]

        # Validate that no file path is absolute
        # for f in files:
        #     if f.startswith('/'):
        #         return False, f"Absolute paths not allowed in tail: {f}"

        # If no files are given, tail will read from stdin.
        cmd = ['tail'] + options + files
        output = subprocess.run(cmd, capture_output=True, text=True)
        return (True, output.stdout) if output.returncode == 0 else (False, output.stderr)
