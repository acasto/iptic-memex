import re
from session_handler import InteractionAction


class AssistantCommandsAction(InteractionAction):
    """
    Handler for assistant commands with support for referencing labeled code blocks.

    Command Format:
    %%COMMAND_NAME%%
    key1="value1"
    key2=value2
    <optional blank line>
    <any content, including code blocks, etc.>
    %%END%%

    Block Reference Format:
    #[block:identifier]
    ```language
    code content
    ```

    Parsing Logic:
    1. Find all code blocks and their identifiers
    2. Find all command blocks
    3. If a command references a block, substitute the block content
    4. Process command normally
    """
    def __init__(self, session):
        self.session = session
        self.params = session.get_params()

        cmd_tool = session.conf.get_option('TOOLS', 'cmd_tool', fallback='assistant_cmd_tool')
        search_tool = session.conf.get_option('TOOLS', 'search_tool', fallback='assistant_websearch_tool')

        self.commands = {
            "CMD": {
                "args": ["command", "arguments"],
                'auto_submit': True,
                "function": {"type": "action", "name": cmd_tool}
            },
            "MATH": {
                "args": ["bc_flags", "expression"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_math_tool"}
            },
            "MEMORY": {
                "args": ["action", "memory", "project", "id"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_memory_tool"}
            },
            "FILE": {
                "args": ["mode", "file", "new_name", "recursive", "block"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_file_tool"}
            },
            "WEBSEARCH": {
                "args": ["query", "recency"],
                'auto_submit': True,
                "function": {"type": "action", "name": search_tool}
            },
            "YOUTRACK": {
                "args": ["mode", "project_id", "issue_id", "description", "summary", "query", "assignee", "state", "priority", "type", "block"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_youtrack_tool"}
            }
        }
        # Check for and load user commands
        user_commands = self.session.get_action('register_assistant_commands')
        if user_commands:
            new_commands = user_commands.run()
            if new_commands:
                self.commands.update(new_commands)

    def run(self, response: str = None):
        # Extract labeled blocks first
        blocks = self.extract_labeled_blocks(response)

        # Parse commands in the response
        parsed_commands = self.parse_commands(response)

        # Process commands
        auto_submit = None  # Track if we should auto-submit after all commands
        for cmd in parsed_commands:
            command_name = cmd['command']
            if command_name in self.commands:
                # Check if command references a block and handle substitution
                if 'block' in cmd['args']:
                    block_id = cmd['args'].pop('block')  # Remove block arg after using
                    if block_id in blocks:
                        # Append block content to any existing content
                        block_content = blocks[block_id]
                        cmd['content'] = cmd['content'] + "\n" + block_content if cmd['content'] else block_content

                command_info = self.commands[command_name]
                # Check auto-submit status
                allow_auto_submit = self.session.conf.get_option('TOOLS', 'allow_auto_submit', fallback=False)
                if command_info.get('auto_submit') and auto_submit is not False and allow_auto_submit:
                    self.session.set_flag('auto_submit', True)

                # Run the command with interrupt handling
                handler = command_info["function"]
                try:
                    # Stop any existing spinner before starting a new one
                    self.session.utils.output.stop_spinner()
                    with self.session.utils.output.spinner("Running command..."):
                        if handler["type"] == "method":
                            method = getattr(self, handler["name"])
                            method(cmd['args'], cmd['content'])
                        else:
                            action = self.session.get_action(handler["name"])
                            action.run(cmd['args'], cmd['content'])
                except KeyboardInterrupt:
                    self.session.utils.output.stop_spinner()
                    self.session.utils.output.write()
                    try:
                        user_input = self.session.utils.input.get_input(
                            self.session.utils.output.style_text(
                                "Hit Ctrl-C again to quit or Enter to continue: ",
                                fg='red'
                            ),
                            allow_empty=True  # Allow empty input without retry
                        )
                        if user_input.strip():
                            continue
                        # Add cancellation context for the assistant
                        self.session.add_context('assistant', {
                            'name': 'command_error',
                            'content': f"Command '{command_name}' was cancelled by user"
                        })
                    except KeyboardInterrupt:
                        self.session.utils.output.write()
                        self.session.get_action('persist_stats').run()
                        raise
                    continue

        # Final processing: if highlighting is True and code blocks are present, reprint chat
        if '```' in response:
            if 'highlighting' in self.params and self.params['highlighting'] is True:
                self.session.get_action('reprint_chat').run()

    @staticmethod
    def extract_labeled_blocks(text: str) -> dict:
        """
        Extract labeled code blocks from the text.
        Returns a dict mapping block identifiers to their content.
        """
        if not text:
            return {}

        # Pattern to match the block identifier line
        block_id_pattern = r'#\[block:(\w+)\]\s*\n'

        # Pattern to match code blocks with optional language
        code_block_pattern = r'```\w*\n(.*?)```'

        blocks = {}
        last_pos = 0

        while True:
            # Find the next block identifier
            id_match = re.search(block_id_pattern, text[last_pos:], re.MULTILINE)
            if not id_match:
                break

            # Adjust position to start of identifier match
            block_start = last_pos + id_match.end()

            # Find the code block that follows
            code_match = re.search(code_block_pattern, text[block_start:], re.DOTALL)
            if not code_match:
                break

            # Extract identifier and content
            block_id = id_match.group(1)
            block_content = code_match.group(1).strip()

            # Store block
            blocks[block_id] = block_content

            # Move position past this block
            last_pos = block_start + code_match.end()

        return blocks

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

            # Extract args and content with clean line handling
            args, command_content = self.extract_args_and_content(lines, known_args)

            command_stack.append({
                'command': command_name,
                'args': args,
                'content': command_content.strip()  # Remove any trailing whitespace from the final content
            })

        return command_stack

    @staticmethod
    def extract_args_and_content(lines, known_args):
        # Remove the trailing %%END%% line if present
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

            # Reconstruct the entire line from pairs to ensure this line is ONLY arguments
            reconstructed = []
            for k1, v1, k2, v2 in candidate_pairs:
                key = k1 if k1 else k2
                val = v1 if k1 else v2
                # If key not in known args, can't treat as argument line
                if key not in known_args:
                    return False
                # We'll just record it to ensure formatting matches arguments only
                if ' ' in val:
                    # If there's a space in val not enclosed in quotes, check if it's from the quoted pattern.
                    # Actually, we've matched quotes in the regex, so no further check is needed.
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
                # This line is not a pure argument line, so treat it and all later lines as content
                content_start = i
                break

        if content_start is None:
            # No non-argument line found, content might be empty
            content_start = len(lines)

        content = '\n'.join(lines[content_start:])
        return args, content
