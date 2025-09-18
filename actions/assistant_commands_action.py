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
        # Build dynamic registry from tool action classes
        self.commands = self._build_commands_dynamic()

    # ---- Dynamic registry construction ------------------------------------
    def _build_commands_dynamic(self) -> dict:
        # Helper: parse comma lists to lowercase tool names
        def _parse_list(val: str | None) -> list[str]:
            if not val:
                return []
            try:
                return [x.strip().lower() for x in str(val).split(',') if x.strip()]
            except Exception:
                return []

        # Resolve allow/block lists with interactive vs non-interactive split
        # Interactive (chat/web/tui): use [TOOLS] allow/deny unchanged
        # Non-interactive (agent/completion): allow = CLI --tools (active_tools_agent) → [AGENT].active_tools → [TOOLS].active_tools
        # and always subtract [AGENT].blocked_tools (hard block)
        non_interactive = False
        try:
            non_interactive = bool(getattr(self.session, 'in_agent_mode', lambda: False)() or self.session.get_flag('completion_mode', False))
        except Exception:
            non_interactive = False

        active = _parse_list(self.session.get_option('TOOLS', 'active_tools', fallback=None))
        inactive = _parse_list(self.session.get_option('TOOLS', 'inactive_tools', fallback=None))
        blocked: list[str] = []

        if non_interactive:
            # CLI override uses a distinct key so it doesn't affect interactive runs
            override_raw = self.session.get_option('AGENT', 'active_tools_agent', fallback=None)
            override_none = False
            if isinstance(override_raw, str) and override_raw.strip().lower() == '__none__':
                override_none = True
                a_allow: list[str] = []
            else:
                a_allow = _parse_list(override_raw)

            if override_none:
                active = []  # Force no tools
            elif a_allow:
                active = a_allow
            else:
                a_active = _parse_list(self.session.get_option('AGENT', 'active_tools', fallback=None))
                if a_active:
                    active = a_active
                else:
                    # Fallback to interactive defaults if agent defaults unset
                    active = _parse_list(self.session.get_option('TOOLS', 'active_tools', fallback=None))

            # Hard blocklist for non-interactive
            blocked = _parse_list(self.session.get_option('AGENT', 'blocked_tools', fallback=None))
            # Non-interactive path ignores [TOOLS].inactive_tools; use [AGENT].blocked_tools instead
            inactive = []

        # Discover all available assistant tool action classes
        try:
            registry = getattr(self.session, '_registry', None)
            available = registry.list_available_actions() if registry else []
        except Exception:
            available = []

        # Filter to actions that look like tools (allow user actions too)
        action_names = [a for a in available if a.endswith('_tool')]

        # Map discovered classes to their tool names/specs
        tools: dict[str, dict] = {}
        for action_name in action_names:
            try:
                cls = registry.get_action_class(action_name) if registry else None
                if not cls:
                    continue
                # Require tool metadata contract
                if not hasattr(cls, 'tool_name') or not hasattr(cls, 'tool_spec'):
                    continue
                name = str(cls.tool_name()).strip().lower()
                if not name:
                    continue
                # Apply can_run gating if provided
                can = True
                try:
                    cr = getattr(cls, 'can_run', None)
                    if callable(cr):
                        can = bool(cr(self.session))
                except Exception:
                    can = True
                if not can:
                    continue

                # Respect allow/deny filters
                # Apply non-interactive hard block first
                if non_interactive and blocked and name in set(blocked):
                    continue
                # Apply allow/deny logic
                if active and name not in active:
                    continue
                if not active and inactive and name in set(inactive):
                    continue

                # Compute handler override: [TOOLS].<tool>_tool
                handler = self.session.get_option('TOOLS', f"{name}_tool", fallback=action_name)

                spec = dict(cls.tool_spec(self.session) or {})
                # Inject function mapping
                spec['function'] = {"type": "action", "name": handler}
                tools.setdefault(name, spec)

                # Register aliases when provided (map to same spec)
                try:
                    aliases = getattr(cls, 'tool_aliases', lambda: [])()
                    for alias in (aliases or []):
                        alias_key = str(alias).strip().lower()
                        if not alias_key:
                            continue
                        if non_interactive and blocked and alias_key in set(blocked):
                            continue
                        if active and alias_key not in active:
                            continue
                        if not active and inactive and alias_key in set(inactive):
                            continue
                        tools.setdefault(alias_key, spec)
                except Exception:
                    pass
            except Exception:
                continue

        # Merge user-defined/overridden commands if present
        try:
            user_commands = self.session.get_action('register_assistant_commands')
            if user_commands:
                new_commands = user_commands.run()
                if isinstance(new_commands, dict) and new_commands:
                    for name, cfg in new_commands.items():
                        key = str(name).strip().lower()
                        # Apply gating to user-provided tools as well
                        if active and key not in active:
                            continue
                        if not active and inactive and key in set(inactive):
                            continue
                        if key in tools and isinstance(tools[key], dict) and isinstance(cfg, dict):
                            merged = dict(tools[key])
                            merged.update(cfg)
                            tools[key] = merged
                        else:
                            tools[key] = cfg
        except Exception:
            pass

        # Merge session dynamic tools (e.g., MCP-registered) if present
        try:
            dyn_tools = self.session.get_user_data('__dynamic_tools__') or {}
            if isinstance(dyn_tools, dict) and dyn_tools:
                for key, cfg in dyn_tools.items():
                    name = str(key).strip().lower()
                    if not name or not isinstance(cfg, dict):
                        continue
                    # Accept as-is, but ensure a function mapping exists
                    if 'function' not in cfg:
                        continue
                    # For non-interactive, still respect the hard blocklist
                    if non_interactive and blocked and name in set(blocked):
                        continue
                    # When an explicit allow-list is set, include only if present
                    if non_interactive and active and name not in set(active):
                        continue
                    tools[name] = dict(cfg)
        except Exception:
            pass

        return tools

    # ---- Canonical tool specs for providers ----
    def _auto_description(self, cmd_key: str, handler_name: str) -> str:
        key = (cmd_key or '').strip()
        handler = (handler_name or '').strip()
        return f"Assistant command {key} mapped to action '{handler}'."

    def get_tool_specs(self) -> list:
        """Return canonical, provider-agnostic tool specs derived from the registry.

        Shape per spec:
          { name, description, parameters: {type:'object', properties:{...}, required:[...]} }
        """
        import re, json as _json

        # 1) Build raw entries with canonical names and function identity
        entries = []
        for cmd_key, info in (self.commands or {}).items():
            try:
                handler = (info.get('function') or {}).get('name', '')
                canonical_name = str(cmd_key).lower()
                desc = info.get('description') or self._auto_description(canonical_name, handler)
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
                required = info['required'] if 'required' in info else []
                params = {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': True}
                fn_key = _json.dumps(info.get('function') or {}, sort_keys=True)
                entries.append({'canonical_name': canonical_name, 'desc': desc, 'parameters': params, 'fn_key': fn_key})
            except Exception:
                continue

        # 2) Group by function identity and choose an API-safe name per function
        def is_ok(n: str) -> bool:
            try:
                return bool(re.match(r'^[A-Za-z0-9_-]+$', n or ''))
            except Exception:
                return False

        groups: dict[str, list[dict]] = {}
        for e in entries:
            groups.setdefault(e['fn_key'], []).append(e)

        name_map: dict[str, str] = {}
        used: set[str] = set()
        out_specs: list = []

        for fn_key, items in groups.items():
            # Prefer an existing alias with a valid name; otherwise sanitize the first canonical
            chosen = None
            for it in items:
                if is_ok(it['canonical_name']):
                    chosen = it
                    break
            if chosen is None:
                chosen = items[0]
            api_name = chosen['canonical_name']
            if not is_ok(api_name):
                base = re.sub(r'[^A-Za-z0-9_-]', '_', api_name) or 'tool'
                api_name = base
                i = 1
                while api_name in used:
                    api_name = f"{base}_{i}"
                    i += 1
            # Record mapping for provider tool_call → canonical name
            name_map[api_name] = chosen['canonical_name']
            used.add(api_name)
            out_specs.append({
                'name': api_name,
                'description': chosen['desc'],
                'parameters': chosen['parameters'],
                'function': fn_key,  # keep for provider-side optional dedup/debug
                'canonical_name': chosen['canonical_name'],
            })

        # Store mapping for providers to translate tool_call names back
        try:
            self.session.set_user_data('__tool_api_to_cmd__', name_map)
        except Exception:
            pass
        return out_specs

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
            command_name = (cmd['command'] or '').lower()
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

                # Merge fixed_args (if any) before running
                handler = command_info["function"]
                try:
                    fixed = handler.get('fixed_args') if isinstance(handler, dict) else None
                    if isinstance(fixed, dict) and fixed:
                        merged_args = dict(cmd['args'] or {})
                        # Fixed args should override user-provided to enforce pinning
                        merged_args.update(fixed)
                        cmd['args'] = merged_args
                except Exception:
                    pass
                # Run the command with interrupt handling
                try:
                    # Stop any existing spinner before starting a new one
                    self.session.utils.output.stop_spinner()
                    # In agent mode, avoid interactive spinners/noise
                    if self.session.in_agent_mode():
                        from contextlib import nullcontext
                        spinner_cm = nullcontext()
                        scope_callable = getattr(self.session.utils.output, 'tool_scope', None)
                        if callable(scope_callable):
                            scope_cm = scope_callable((command_name or '').lower(), call_id=None, title=None)
                        else:
                            scope_cm = nullcontext()
                    else:
                        # Try to provide a helpful one-line summary for the spinner
                        try:
                            summary = None
                            a = cmd.get('args') or {}
                            desc = None
                            d = a.get('desc') if isinstance(a, dict) else None
                            if isinstance(d, str):
                                d = d.strip()
                            if not d and isinstance(a, dict):
                                if (command_name or '').lower() == 'cmd':
                                    c = a.get('command') or ''
                                    s = a.get('arguments') or ''
                                    joined = (f"{c} {s}" if c else '').strip()
                                    if not joined:
                                        joined = (cmd.get('content') or '').strip()
                                    if joined:
                                        d = joined
                            if isinstance(d, str) and len(d) > 120:
                                d = d[:117] + '...'
                            desc = d
                            msg = f"Tool calling: {(command_name or '').lower()}" + (f" — {d}" if d else "")
                        except Exception:
                            msg = f"Tool calling: {(command_name or '').lower()}"
                            desc = None
                        spinner_cm = self.session.utils.output.spinner(msg)
                        scope_callable = getattr(self.session.utils.output, 'tool_scope', None)
                        if callable(scope_callable):
                            scope_cm = scope_callable((command_name or '').lower(), call_id=None, title=desc)
                        else:
                            from contextlib import nullcontext
                            scope_cm = nullcontext()

                    # Log pseudo-tool begin
                    try:
                        self.session.utils.logger.tool_begin(name=(command_name or '').lower(), call_id=None, args_summary=cmd.get('args') or {}, source='pseudo')
                    except Exception:
                        pass
                    with spinner_cm:
                        with scope_cm:
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
                                try:
                                    self.session.utils.logger.tool_end(name=(command_name or '').lower(), call_id=None, status='error', result_meta={'error': str(need_exc)})
                                except Exception:
                                    pass
                                raise
                    # Successful end
                    try:
                        self.session.utils.logger.tool_end(name=(command_name or '').lower(), call_id=None, status='success')
                    except Exception:
                        pass
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
        known_command_names = list(self.commands.keys())
        if not known_command_names:
            return []  # No commands are registered, so nothing to parse.

        # Create a specific regex for just the command start tags
        command_group = '|'.join([re.escape(k) for k in known_command_names])
        command_start_pattern = re.compile(rf'^[ \t]*%%({command_group})%%[ \t]*$', re.IGNORECASE)

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
                        command_info = self.commands.get((command_name or '').lower(), {})
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
