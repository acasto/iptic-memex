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
                "description": "Execute a local shell command. Provide the program in 'command' and an optional space-delimited string in 'arguments'.",
                "required": ["command"],
                "schema": {
                    "properties": {
                        "command": {"type": "string", "description": "Program to execute (e.g., 'echo', 'grep')."},
                        "arguments": {"type": "string", "description": "Space-delimited arguments string (quoted as needed)."},
                        "content": {"type": "string", "description": "Unused for CMD; include arguments in 'arguments'."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": cmd_tool}
            },
            "MATH": {
                "args": ["bc_flags", "expression"],
                "description": "Evaluate arithmetic with the 'bc' calculator. Provide the expression; optional 'bc_flags' like '-l' enable math library or scale.",
                "required": ["expression"],
                "schema": {
                    "properties": {
                        "bc_flags": {"type": "string", "description": "Flags for bc (e.g., '-l' for math library)."},
                        "expression": {"type": "string", "description": "Expression to evaluate; if omitted, 'content' is used."},
                        "content": {"type": "string", "description": "Expression fallback when 'expression' is not set."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_math_tool"}
            },
            "OPENLINK": {
                "args": ["url", "urls"],
                "description": "Open one or more HTTP/HTTPS links in the user's default browser. Provide a single 'url', a comma-separated 'urls', or list URLs on separate lines in content.",
                "required": [],
                "schema": {
                    "properties": {
                        "url": {"type": "string", "description": "Single URL to open; protocol auto-added if missing."},
                        "urls": {"type": "string", "description": "Comma-separated list of URLs to open."},
                        "content": {"type": "string", "description": "Optional newline-separated URLs to open; lines starting with # are ignored."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_openlink_tool"}
            },
            "MEMORY": {
                "args": ["action", "memory", "project", "id"],
                "description": "Save, read, or clear short memories in a local store. Use action=save|read|clear with optional 'project' scope and 'id' for specific records.",
                "required": ["action"],
                "schema": {
                    "properties": {
                        "action": {"type": "string", "enum": ["save", "read", "clear"], "description": "Operation to perform."},
                        "memory": {"type": "string", "description": "Memory text to save (or use 'content')."},
                        "project": {"type": "string", "description": "Project scope (use 'all' with action=read|clear)."},
                        "id": {"type": "string", "description": "Specific memory ID for read/clear."},
                        "content": {"type": "string", "description": "Memory text fallback when 'memory' is not set."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_memory_tool"}
            },
            "FILE": {
                "args": ["mode", "file", "new_name", "recursive", "block"],
                "description": "Read or modify files in the workspace. Modes: read, write, append, edit, summarize, delete, rename, copy. Use 'content' for write/append/edit.",
                "required": ["mode", "file"],
                "schema": {
                    "properties": {
                        "mode": {"type": "string", "enum": ["read", "write", "append", "edit", "summarize", "delete", "rename", "copy"], "description": "Operation to perform."},
                        "file": {"type": "string", "description": "Target file path (relative to workspace)."},
                        "new_name": {"type": "string", "description": "New name/path for rename or copy."},
                        "recursive": {"type": "boolean", "description": "When deleting, remove directories recursively if true."},
                        "block": {"type": "string", "description": "Identifier of a %%BLOCK:...%% to append to 'content'."},
                        "content": {"type": "string", "description": "Content to write/append or edit instructions (for edit mode)."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_file_tool"}
            },
            "WEBSEARCH": {
                "args": ["query", "recency", "domains", "mode"],
                "description": "Search the web. Provide 'query'. Optional 'mode' can be 'basic' or 'advanced'.",
                "required": ["query"],
                "schema": {
                    "properties": {
                        "query": {"type": "string", "description": "Search query text."},
                        "recency": {"type": "string", "description": "Recency filter (e.g., 'day', 'week', 'month')."},
                        "domains": {"type": "string", "description": "Comma-separated domain filter list (e.g., 'example.com,another.com')."},
                        "mode": {"type": "string", "enum": ["basic", "advanced"], "description": "Search mode: 'basic' for simple queries or 'advanced' for deeper analysis."},
                        "content": {"type": "string", "description": "Additional terms appended to the query."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": search_tool}
            },
            "YOUTRACK": {
                "args": ["mode", "project_id", "issue_id", "block", "summary", "query", "assignee", "state", "priority", "type"],
                "description": "Interact with YouTrack: list projects/issues, fetch details, create and update issues, or add comments. Configure base_url and api_key in settings.",
                "required": ["mode"],
                "schema": {
                    "properties": {
                        "mode": {"type": "string", "enum": ["get_projects", "get_issues", "get_issue_details", "create_issue", "update_summary", "update_description", "assign_issue", "update_state", "update_priority", "update_type", "add_comment"], "description": "Operation to perform."},
                        "project_id": {"type": "string", "description": "Project short name (e.g., 'PROJ')."},
                        "issue_id": {"type": "string", "description": "Issue idReadable (e.g., 'PROJ-123')."},
                        "summary": {"type": "string", "description": "Issue summary for create/update."},
                        "query": {"type": "string", "description": "Additional query/filter terms."},
                        "assignee": {"type": "string", "description": "Assignee username/display name for assignment."},
                        "state": {"type": "string", "description": "New issue state/status."},
                        "priority": {"type": "string", "description": "New issue priority."},
                        "type": {"type": "string", "description": "Issue type (e.g., Bug, Task)."},
                        "block": {"type": "string", "description": "Identifier of a %%BLOCK:...%% to append to 'content'."},
                        "content": {"type": "string", "description": "Longer text for description or comment where applicable."}
                    }
                },
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_youtrack_tool"}
            }
        }
        # Check for and load user commands
        user_commands = self.session.get_action('register_assistant_commands')
        if user_commands:
            new_commands = user_commands.run()
            if isinstance(new_commands, dict) and new_commands:
                # Shallow per-command merge: allow users to override only selected fields
                for name, cfg in new_commands.items():
                    if name in self.commands and isinstance(self.commands[name], dict) and isinstance(cfg, dict):
                        merged = dict(self.commands[name])
                        merged.update(cfg)
                        self.commands[name] = merged
                    else:
                        self.commands[name] = cfg

    # ---- Canonical tool specs for providers ----
    def _auto_description(self, cmd_key: str, handler_name: str) -> str:
        key = (cmd_key or '').strip()
        handler = (handler_name or '').strip()
        return f"Assistant command {key} mapped to action '{handler}'."

    def _infer_required(self, cmd_key: str) -> list:
        k = (cmd_key or '').upper()
        if k == 'CMD':
            return ['command']
        if k == 'FILE':
            return ['mode', 'file']
        if k == 'WEBSEARCH':
            return ['query']
        if k == 'OPENLINK':
            return ['url']
        if k == 'YOUTRACK':
            return ['mode']
        if k == 'MATH':
            return ['expression']
        if k == 'MEMORY':
            return ['action']
        return []

    def get_tool_specs(self) -> list:
        """Return canonical, provider-agnostic tool specs derived from the registry.

        Shape per spec:
          { name, description, parameters: {type:'object', properties:{...}, required:[...]} }
        """
        specs = []
        for cmd_key, info in (self.commands or {}).items():
            try:
                handler = (info.get('function') or {}).get('name', '')
                name = str(cmd_key).lower()
                desc = info.get('description') or self._auto_description(cmd_key, handler)
                arg_names = list(info.get('args', []) or [])

                # Default properties: strings for all declared args + freeform content
                properties = {a: {"type": "string"} for a in arg_names}
                properties['content'] = {"type": "string"}

                # Merge schema overrides when present
                schema = info.get('schema') or {}
                schema_props = schema.get('properties') or {}
                if isinstance(schema_props, dict):
                    for k, v in schema_props.items():
                        try:
                            properties[k] = v
                        except Exception:
                            continue

                required = info.get('required') or self._infer_required(cmd_key)

                specs.append({
                    'name': name,
                    'description': desc,
                    'parameters': {
                        'type': 'object',
                        'properties': properties,
                        'required': required,
                        'additionalProperties': True,
                    }
                })
            except Exception:
                continue
        return specs

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
                    # In agent mode, avoid interactive spinners/noise
                    if self.session.in_agent_mode():
                        from contextlib import nullcontext
                        spinner_cm = nullcontext()
                    else:
                        spinner_cm = self.session.utils.output.spinner("Running command...")

                    with spinner_cm:
                        if handler["type"] == "method":
                            method = getattr(self, handler["name"])
                            method(cmd['args'], cmd['content'])
                        else:
                            action = self.session.get_action(handler["name"])
                            try:
                                action.run(cmd['args'], cmd['content'])
                            except Exception as need_exc:
                                # Annotate InteractionNeeded with action + args so Web can resume correctly
                                from base_classes import InteractionNeeded
                                if isinstance(need_exc, InteractionNeeded):
                                    try:
                                        spec = dict(getattr(need_exc, 'spec', {}) or {})
                                        spec['__action__'] = handler["name"]
                                        spec['__args__'] = cmd['args']
                                        spec['__content__'] = cmd['content']
                                        need_exc.spec = spec
                                    except Exception:
                                        pass
                                raise
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
        # Gate reprint to blocking UIs (CLI). In Web/TUI, avoid stdout clearing/prints.
        if '```' in response:
            # Get fresh params to check highlighting setting
            params = self.session.get_params()
            ui = getattr(self.session, 'ui', None)
            blocking = True
            try:
                blocking = bool(ui and ui.capabilities and ui.capabilities.blocking)
            except Exception:
                blocking = True
            if blocking and params.get('highlighting') is True:
                self.session.get_action('reprint_chat').run()

    @staticmethod
    def _sanitize_think_sections(text: str) -> str:
        """
        Remove <think>/<thinking> ... </think></thinking> segments from text.
        Edge cases handled:
          - Stray closing tag before any opener: drop up to and including that closer.
          - Unclosed open tag: drop from opener to end.
        """
        if not text:
            return text

        out = []
        i = 0
        n = len(text)
        open_tags = ('<think>', '<thinking>')
        close_tags = ('</think>', '</thinking>')
        open_lens = {t: len(t) for t in open_tags}
        close_lens = {t: len(t) for t in close_tags}
        in_think = False

        while i < n:
            if not in_think:
                # Earliest opener among supported tags
                next_open = -1
                open_hit = None
                for t in open_tags:
                    pos = text.find(t, i)
                    if pos != -1 and (next_open == -1 or pos < next_open):
                        next_open = pos
                        open_hit = t
                # Earliest closer among supported tags
                next_close = -1
                close_hit = None
                for t in close_tags:
                    pos = text.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_hit = t

                if next_open == -1 and next_close == -1:
                    out.append(text[i:])
                    break

                # Handle stray close appearing before any open: drop from current i to after close
                if next_close != -1 and (next_open == -1 or next_close < next_open):
                    i = next_close + close_lens[close_hit]
                    continue

                # Normal open
                if next_open != -1 and (next_close == -1 or next_open <= next_close):
                    out.append(text[i:next_open])
                    in_think = True
                    i = next_open + open_lens[open_hit]
                    continue

                # Fallback: append remainder
                out.append(text[i:])
                break
            else:
                # We are inside a think segment; look for the close tag
                next_close = -1
                close_hit = None
                for t in close_tags:
                    pos = text.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_hit = t
                if next_close == -1:
                    # Unclosed think: drop until end
                    i = n
                else:
                    i = next_close + close_lens[close_hit]
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

        # Defensive cleanup: drop a trailing line that is a lone '%'.
        # This guards against rare parser edge cases where a split '%%END%%'
        # across streaming chunks could leak a single '%' into content capture.
        if content:
            parts = content.splitlines()
            # Remove trailing blank lines for inspection, preserve original otherwise
            idx = len(parts) - 1
            while idx >= 0 and parts[idx].strip() == "":
                idx -= 1
            if idx >= 0 and parts[idx].strip() == '%':
                # Drop the stray '%', keep preceding lines (including prior blanks)
                parts = parts[:idx]
                content = '\n'.join(parts)

        return args, content
