import re
from session_handler import InteractionAction


class AssistantCommandsAction(InteractionAction):
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
            "CMD": {
                "args": ["command", "arguments"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_cmd_tool"}
            },
            "MATH": {
                "args": ["bc_flags", "expression"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_math_tool"}
            },
            "MEMORY": {
                "args": ["action", "memory"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_memory_tool"}
            },
            "FILE": {
                "args": ["mode", "file"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_file_tool"}
            }
        }
        # Check for and load user commands
        user_commands = self.session.get_action('register_assistant_commands')
        if user_commands:
            new_commands = user_commands.run()
            if new_commands:
                self.commands.update(new_commands)

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
                    self.session.set_flag('auto_submit', True)

                # Run the command
                handler = command_info["function"]
                # self.session.utils.output.write()
                with self.session.utils.output.spinner("Running command..."):
                    if handler["type"] == "method":
                        method = getattr(self, handler["name"])
                        method(cmd['args'], cmd['content'])
                    else:
                        action = self.session.get_action(handler["name"])
                        action.run(cmd['args'], cmd['content'])

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
