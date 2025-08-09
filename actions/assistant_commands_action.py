import re
from base_classes import InteractionAction


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
    1. Extract all labeled code blocks from the response and map identifiers to content
    2. Parse all command blocks and their arguments/content
    3. For commands with a 'block' argument:
        * Look up the referenced block content by identifier
        * Remove the 'block' argument from the args dict
        * Append the block content to the command's content parameter
    4. Execute the command with the resolved content
    """
    def __init__(self, session):
        self.session = session

        cmd_tool = session.get_option('TOOLS', 'cmd_tool', fallback='assistant_cmd_tool')
        search_tool = session.get_option('TOOLS', 'search_tool', fallback='assistant_websearch_tool')

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
            "OPENLINK": {
                "args": ["url"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_openlink_tool"}
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
                "args": ["mode", "project_id", "issue_id", "block", "summary", "query", "assignee", "state", "priority", "type"],
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
        # Backstop: sanitize out <think> ... </think> segments so parser
        # never considers tools mentioned inside thinking sections.
        sanitized = self._sanitize_think_sections(response or "")

        # Extract labeled blocks first (from sanitized text)
        blocks = self.extract_labeled_blocks(sanitized)

        # Parse commands in the sanitized response
        parsed_commands = self.parse_commands(sanitized)

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
                allow_auto_submit = self.session.get_option('TOOLS', 'allow_auto_submit', fallback=False)
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
            # Get fresh params to check highlighting setting
            params = self.session.get_params()
            if 'highlighting' in params and params['highlighting'] is True:
                self.session.get_action('reprint_chat').run()

    @staticmethod
    def _sanitize_think_sections(text: str) -> str:
        """
        Remove <think> ... </think> segments from text.
        Also handles edge cases:
          - Stray closing </think> with no prior <think>: drop from start to that closer.
          - Unclosed <think> with no </think>: drop from opener to end.
        """
        if not text:
            return text

        out = []
        i = 0
        n = len(text)
        open_tag = '<think>'
        close_tag = '</think>'
        lo = len(open_tag)
        lc = len(close_tag)
        in_think = False

        while i < n:
            if not in_think:
                next_open = text.find(open_tag, i)
                next_close = text.find(close_tag, i)

                if next_open == -1 and next_close == -1:
                    out.append(text[i:])
                    break

                # Handle stray close appearing before any open: drop from current i to after close
                if next_close != -1 and (next_open == -1 or next_close < next_open):
                    i = next_close + lc
                    continue

                # Normal open
                if next_open != -1 and (next_close == -1 or next_open <= next_close):
                    out.append(text[i:next_open])
                    in_think = True
                    i = next_open + lo
                    continue

                # Fallback: append remainder
                out.append(text[i:])
                break
            else:
                # We are inside a think segment; look for the close tag
                next_close = text.find(close_tag, i)
                if next_close == -1:
                    # Unclosed think: drop until end
                    i = n
                else:
                    i = next_close + lc
                    in_think = False

        return ''.join(out)

    @staticmethod
    def extract_labeled_blocks(text: str) -> dict:
        """
        Extract labeled code blocks from the text.
        Returns a dict mapping block identifiers to their content.

        Block format:
        %%BLOCK:identifier%%
        ...content...
        %%END%%
        """
        if not text:
            return {}

        # Pattern to match blocks with the new format
        block_pattern = (
            r'(?m)'
            r'^[ \t]*%%BLOCK:(\w+)%%[ \t]*\n'  # Opening line with identifier
            r'([\s\S]*?)'  # Content (non-greedy)
            r'^[ \t]*%%END%%[ \t]*$'  # Closing line
        )

        blocks = {}

        # Find all blocks in the text
        for match in re.finditer(block_pattern, text):
            block_id = match.group(1)
            block_content = match.group(2).strip()
            blocks[block_id] = block_content

        return blocks

    # noinspection PyUnresolvedReferences
    def parse_commands(self, text: str):
        # Normalize line endings first
        text = text.replace('\r\n', '\n')
        lines = text.splitlines()
        command_stack = []

        # Get the list of known, valid command names to look for
        known_command_names = self.commands.keys()
        if not known_command_names:
            return []  # No commands are registered, so nothing to parse.

        # Create a specific regex for just the command start tags
        command_group = '|'.join(known_command_names)
        command_start_pattern = re.compile(rf'^[ \t]*%%({command_group})%%[ \t]*$')

        # Create a simple regex for the end tag
        end_pattern = re.compile(r'^[ \t]*%%END%%[ \t]*$')

        i = 0
        while i < len(lines):
            line = lines[i]
            match = command_start_pattern.match(line)

            if match:
                # Check if this line appears to be quoted or in a code block
                # Look for quotes or backticks at the start of the line (ignoring whitespace)
                line_start = line.lstrip()
                if (line_start.startswith('"') or
                        line_start.startswith("'") or
                        line_start.startswith('`')):
                    i += 1
                    continue

                # Found the start of a valid command block
                command_name = match.group(1)
                content_lines = []

                # Now, loop forward to find the corresponding %%END%%
                j = i + 1
                while j < len(lines):
                    if end_pattern.match(lines[j]):
                        # Found the end of the block.
                        # Get command info and known args
                        command_info = self.commands.get(command_name, {})
                        known_args = command_info.get("args", [])

                        # Extract args and content from the captured lines
                        args, command_content = self.extract_args_and_content(content_lines, known_args)

                        command_stack.append({
                            'command': command_name,
                            'args': args,
                            'content': command_content.strip()
                        })

                        # Move the outer loop's index past this entire block
                        i = j
                        break  # Exit the inner 'j' loop
                    else:
                        # This line is part of the command's content
                        content_lines.append(lines[j])
                    j += 1

                # Check if we exited the loop without finding %%END%%
                if j >= len(lines):
                    # Command was never closed - skip it
                    i = j - 1

            i += 1

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
