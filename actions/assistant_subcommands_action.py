import re
import tempfile
import subprocess
from session_handler import InteractionAction


class AssistantSubcommandsAction(InteractionAction):
    """
    A revised version of the assistant commands action with a new command format.

    Command Format:
    We use a start and end delimiter to clearly define the command block.
    Example:

    %%COMMAND_NAME%%
    key1="value1"
    key2=value2
    <optional blank line>
    <any content, including code blocks, etc.>
    %%END%%

    Parsing Logic:
    1. Find all occurrences of blocks starting with %%COMMAND_NAME%% and ending with %%END%%.
    2. Extract command_name from the first line (COMMAND_NAME must match a known command).
    3. Subsequent lines until a blank line or non-argument line are treated as arguments.
       - Arguments must be defined in self.commands[command_name]["args"] to be parsed as args.
    4. Everything after that is considered content and passed as a single string.
    5. Dispatch to the appropriate handler defined in self.commands.

    Security & Validation:
    - Only parse arguments that match known argument keys.
    - Lines that don’t strictly match argument lines or contain unknown keys become content.

    Extensibility:
    - To add a new command, define it in self.commands with args, and a function.
    - The handler receives a dict of args and a content string.
    """

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.commands = {
            "DATETIME": {
                "args": [],
                'auto_submit': True,
                "function": {"type": "method", "name": "handle_datetime_command"}
            },
            "CMD": {
                "args": ["command", "arguments"],
                'auto_submit': True,
                "function": {"type": "method", "name": "handle_shell_command"}
            },
            "MATH": {
                "args": ["bc_flags", "expression"],
                'auto_submit': True,
                "function": {"type": "method", "name": "handle_math"}
            },
            "MEMORY": {
                "args": ["action"],
                'auto_submit': True,
                "function": {"type": "method", "name": "handle_memory_command"}
            },
            "ASK_AI": {
                "args": ["model", "question"],
                "function": {"type": "method", "name": "handle_ask_ai"}
            }
            # Add more commands here as needed.
        }

    def run(self, response: str = None):
        # Parse commands in the response
        parsed_commands = self.parse_commands(response)

        # Process commands
        auto_submit = None  # Track if we should auto-submit after all commands
        for cmd in parsed_commands:
            command_name = cmd['command']
            if command_name in self.commands:
                command_info = self.commands[command_name]
                # Check auto-submit status
                allow_auto_submit = self.session.conf.get_option('TOOLS', 'allow_auto_submit', fallback=False)
                if command_info.get('auto_submit') and auto_submit is not False and allow_auto_submit:
                    auto_submit = True
                elif not command_info.get('auto_submit'):
                    auto_submit = False

                # Run the command
                handler = command_info["function"]
                if handler["type"] == "method":
                    method = getattr(self, handler["name"])
                    method(cmd['args'], cmd['content'])
                else:
                    action = self.session.get_action(handler["name"])
                    action.run(cmd['args'], cmd['content'])

        # Handle auto-submit if allowed and not disabled by any command
        if auto_submit:
            self._submit_assistant_response()
            return

        # Final processing: if highlighting is True and code blocks are present, reprint chat
        # todo - this probably should be, and might have once been, in the print_response action
        if '```' in response:
            if 'highlighting' in self.params and self.params['highlighting'] is True:
                self.session.get_action('reprint_chat').run()

    def parse_commands(self, text: str):
        # Normalize line endings first
        text = text.replace('\r\n', '\n')

        # Enhanced pattern that ensures both command and END are on their own lines
        command_pattern = (
            r'(?m)(?P<block>'
            r'^[ \t]*(?<!["\'`])%%(?P<command>[A-Z0-9_]+)%%[ \t]*\n'  # command line
            r'(?P<content>[\s\S]*?)'                                   # content
            r'^[ \t]*%%END%%[ \t]*$'                                  # end line
            r')'
        )
        blocks = re.finditer(command_pattern, text)

        command_stack = []

        for match in blocks:
            command_name = match.group('command')
            block_content = match.group('content')

            # Split content into lines, removing any leading/trailing blank lines
            lines = [line for line in block_content.splitlines()]

            # Get command info and known args
            command_info = self.commands.get(command_name, {})
            known_args = command_info.get("args", [])

            # Extract args and content, with clean line handling
            args, command_content = self.extract_args_and_content(lines, known_args)

            command_stack.append({
                'command': command_name,
                'args': args,
                'content': command_content.strip()  # Remove any trailing whitespace from final content
            })

        return command_stack

    @staticmethod
    def extract_args_and_content(lines, known_args):
        # Remove trailing %%END%% line if present
        if lines and lines[-1].strip() == '%%END%%':
            lines = lines[:-1]

        args = {}
        content_start = None

        # A line is considered a pure argument line if:
        # 1. It is not empty.
        # 2. Every token matches key="value" or key=value
        # 3. Every key is in known_args
        arg_token_pattern = r'(\w+)="([^"]*)"|(\w+)=(\S+)'

        # noinspection PyShadowingNames
        def is_arg_line(candidate_line):
            stripped = candidate_line.strip()
            if stripped == '':
                # Blank line means end of args
                return False
            # Find all tokens
            candidate_pairs = re.findall(arg_token_pattern, stripped)
            if not candidate_pairs:
                # No argument pairs found
                return False

            # Reconstruct entire line from pairs to ensure this line is ONLY arguments
            reconstructed = []
            for k1, v1, k2, v2 in candidate_pairs:
                key = k1 if k1 else k2
                val = v1 if k1 else v2
                # If key not in known args, can't treat as argument line
                if key not in known_args:
                    return False
                # We'll just record it to ensure formatting matches arguments only
                if ' ' in val:
                    # If there's a space in val not enclosed in quotes, check if it's from quoted pattern.
                    # Actually we've matched quotes in the regex, so no further check needed.
                    pass
                if k1:
                    reconstructed.append(f'{key}="{val}"')
                else:
                    reconstructed.append(f'{key}={val}')

            # Join reconstructed to see if it matches the stripped line exactly in terms of argument formatting
            # We'll allow arguments separated by whitespace. We just need to ensure no extra non-argument stuff.
            # Since we did a global match, if there's extra text that isn't matched as an argument,
            # pairs wouldn't match the entire line. Let's do a quick check to ensure there's no leftover text.
            # Another approach is to use a stricter regex that matches the whole line, but let's do a quick check:
            arg_line_pattern = r'^(\w+="[^"]*"|\w+=\S+)(\s+(\w+="[^"]*"|\w+=\S+))*\s*$'
            if re.match(arg_line_pattern, stripped):
                return True
            return False

        # Parse argument lines first
        for i, line in enumerate(lines):
            if is_arg_line(line):
                # Parse arguments from this line
                pairs = re.findall(arg_token_pattern, line.strip())
                for k1, v1, k2, v2 in pairs:
                    key = k1 if k1 else k2
                    val = v1 if k1 else v2
                    args[key] = val
            else:
                # This line is not a pure argument line, so treat it and all subsequent lines as content
                content_start = i
                break

        if content_start is None:
            # No non-argument line found, content might be empty
            content_start = len(lines)

        content = '\n'.join(lines[content_start:])
        return args, content

    def _submit_assistant_response(self):
        """Handle submission of assistant response to get AI completion"""
        contexts = self.session.get_action('process_contexts').get_contexts(self.session)
        chat = self.session.get_context('chat')

        # Submit the assistant response based on the context
        if contexts:
            processed_context = self.session.get_action('process_contexts').process_contexts_for_assistant(contexts)
            chat.add(processed_context, 'assistant')

        # Clear the contexts after adding them
        for context in list(self.session.get_context().keys()):
            if context != 'prompt' and context != 'chat':
                self.session.remove_context_type(context)

        # Get and print response
        self.session.get_action('print_response').run()

    def handle_datetime_command(self, args, content):
        from datetime import datetime
        timestamp = "Current date and time: " + datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        self.session.add_context('assistant', {'name': 'assistant_context', 'content': timestamp})

    def handle_memory_command(self, args, content):
        if args.get('action') == 'save':
            output = subprocess.run('echo ' + args.get('memory') + ' >> /Users/adam/.config/iptic-memex/memory.txt',
                                    shell=True, capture_output=True, text=True)
        if args.get('action') == 'read':
            output = subprocess.run('cat /Users/adam/.config/iptic-memex/memory.txt', shell=True, capture_output=True,
                                    text=True)
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': output.stdout})

    def handle_math(self, args, content):
        bc_args = args.get('bc_flags', '')
        bc_expression = args.get('expression', content)
        bc_command = ['bc'] + bc_args.split()
        output = self.run_with_temp_file(bc_command, bc_expression)
        self.session.add_context('assistant', {
            'name': 'assistant_context',
            'content': output
        })

    def handle_ask_ai(self, args, content):
        model = args.get('model', 'claude')
        question = args.get('question', '')
        question += '\n' + content if content else ''
        ai_command = [f'memex', '-m', model, '-f', '-']
        output = self.run_with_temp_file(ai_command, question)
        self.session.add_context('assistant', {
            'name': 'assistant_context',
            'content': output
        })

    def handle_shell_command(self, args, content):
        # A dictionary of allowed commands mapped to their handler functions.
        # Each handler function takes a list of arguments and returns (success, output).
        safe_dispatch = {
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
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': output})
        else:
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': f"Error: {output}"})

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

    @staticmethod
    def run_with_temp_file(command, content, mode='w+', encoding='utf-8'):
        kwargs = {'mode': mode}
        if 'b' not in mode and encoding:
            kwargs['encoding'] = encoding

        try:
            with tempfile.NamedTemporaryFile(**kwargs) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                temp_file.seek(0)
                try:
                    output = subprocess.run(
                        command,
                        stdin=temp_file,
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    return output.stdout.strip()
                except subprocess.CalledProcessError as e:
                    return f"Error: Command {' '.join(command)} failed with exit status {e.returncode}\n{e.stderr.strip()}"
        except OSError as e:
            raise OSError(f"Error creating or deleting temporary file: {e}")
