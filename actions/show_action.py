from base_classes import InteractionAction


class ShowAction(InteractionAction):
    """
    A general action for showing/listing things to the user
    """
    def __init__(self, session):
        self.session = session

    def run(self, args: list = None):
        """
        Show things to the user
        """
        if args is None:
            return

        if args[0] == 'settings':
            settings = self.session.get_session_state()
            sorted_params = sorted(settings['params'].items())
            lines = ["Params:", "---------------------------------"]
            for key, value in sorted_params:
                if key == 'api_key':
                    value = '********'
                lines.append(f"{key}: {value}")
            lines.append("")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

        if args[0] == 'tool-settings':
            tools = self.session.get_tools()
            sorted_tools = sorted(tools.items())
            lines = ["Tools:", "---------------------------------"]
            for key, value in sorted_tools:
                lines.append(f"{key}: {value}")
            lines.append("")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

        if args[0] == 'tools':
            """Show currently active assistant tools (after config gating)."""
            try:
                cmd = self.session.get_action('assistant_commands')
                entries = cmd.commands if cmd else {}
                # Deduplicate by function identity and prefer normalized (alias) names.
                # Also annotate MCP dynamic tools with their server label when available.
                import json as _json
                display_map = {}
                if isinstance(entries, dict):
                    for name, spec in entries.items():
                        try:
                            fn = spec.get('function') if isinstance(spec, dict) else None
                            fn_key = _json.dumps(fn or {}, sort_keys=True)
                            # Choose preferred label: prefer non-namespaced alias when duplicates exist
                            current = display_map.get(fn_key)
                            # Build annotated label
                            label = str(name)
                            server = None
                            try:
                                fixed = (fn or {}).get('fixed_args') or {}
                                server = fixed.get('server')
                            except Exception:
                                server = None
                            if server:
                                # For MCP-registered tools, show "name (server)"
                                label = f"{label} ({server})"
                            if current is None:
                                display_map[fn_key] = label
                                continue
                            # Prefer an alias (no namespace) over namespaced canonical keys
                            is_alias = ':' not in name and '/' not in name
                            is_current_alias = ':' not in current and '/' not in current
                            if is_alias and not is_current_alias:
                                display_map[fn_key] = label
                        except Exception:
                            continue
                names = sorted(list(display_map.values())) if display_map else []
                mode = 'agent' if getattr(self.session, 'in_agent_mode', lambda: False)() else 'chat'
                header = f"Active tools ({mode}):"
                lines = [header, "---------------------------------"]
                if names:
                    for n in names:
                        lines.append(n)
                else:
                    lines.append('(none)')
                lines.append("")
                try:
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.session.ui.emit('error', {'message': f'Error showing tools: {e}'})
                except Exception:
                    pass

        if args[0] == 'models':
            sections = list(self.session.list_models().keys())
            try:
                self.session.ui.emit('status', {'message': "\n".join(sections)})
            except Exception:
                pass

        if args[0] == 'messages':
            try:
                # First, show the system prompt if it exists
                prompt_context = self.session.get_context('prompt')
                if prompt_context:
                    prompt_data = prompt_context.get()
                    if prompt_data and prompt_data.get('content'):
                        try:
                            self.session.ui.emit('status', {'message': '=== SYSTEM PROMPT ===\n' + prompt_data['content'] + '\n' + ('='*50) + '\n'})
                        except Exception:
                            pass

                # Then show the conversation messages
                if len(args) > 1 and args[1] == 'all':
                    # Get all messages from chat context
                    chat_context = self.session.get_context('chat')
                    if chat_context:
                        messages = chat_context.get("all")
                    else:
                        try:
                            self.session.ui.emit('status', {'message': 'No chat context available'})
                        except Exception:
                            pass
                        return
                else:
                    # Get messages from provider (which delegates to chat context)
                    provider = self.session.get_provider()
                    if provider:
                        messages = provider.get_messages()
                    else:
                        try:
                            self.session.ui.emit('status', {'message': 'No provider available'})
                        except Exception:
                            pass
                        return
                
                if not messages:
                    try:
                        self.session.ui.emit('status', {'message': 'No conversation messages to display'})
                    except Exception:
                        pass
                    return
                
                # Display messages with proper formatting
                lines = ["=== CONVERSATION ==="]
                for i, message in enumerate(messages):
                    timestamp = message.get('timestamp', 'Unknown time')
                    role = message.get('role', 'unknown')
                    context = message.get('context', None)
                    
                    # Handle different message content formats
                    content = ""
                    
                    # Check for old simple format first
                    if 'message' in message and message['message']:
                        content = message['message']
                    # Check for modern format with content array
                    elif 'content' in message:
                        content_data = message['content']
                        if isinstance(content_data, list):
                            # Extract text from content array
                            text_parts = []
                            for item in content_data:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    text_parts.append(item.get('text', ''))
                                elif isinstance(item, dict) and item.get('type') == 'image_url':
                                    text_parts.append('[IMAGE]')
                            content = ' '.join(text_parts)
                        elif isinstance(content_data, str):
                            content = content_data
                    
                    entry = [f"[{i}] {timestamp} - {role.upper()}"]
                    if content:
                        entry.append(f"    Message: {content}")
                    else:
                        entry.append("    Message: (empty)")

                    # Surface tool-calls metadata for assistant messages (official tools)
                    try:
                        if role == 'assistant' and 'tool_calls' in message and message['tool_calls']:
                            entry.append("    Tool Calls:")
                            for k, tc in enumerate(message['tool_calls']):
                                fn = (tc.get('function') or {}).get('name') if isinstance(tc, dict) else None
                                tc_id = tc.get('id') if isinstance(tc, dict) else None
                                entry.append(f"      ({k}) name={fn or '?'} id={tc_id or '?'}")
                    except Exception:
                        pass
                    
                    if context:
                        entry.append(f"    Context: {len(context)} item(s)")
                        for j, ctx in enumerate(context):
                            ctx_type = ctx.get('type', 'unknown')
                            entry.append(f"      [{j}] Type: {ctx_type}")
                    lines.append("\n".join(entry) + "\n")

            except Exception as e:
                try:
                    self.session.ui.emit('error', {'message': f'Error displaying messages: {e}'})
                except Exception:
                    pass
                return

            # Emit or print assembled lines
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

        if args[0] == 'contexts':
            contexts = self.session.get_action('process_contexts').get_contexts(self.session)
            if len(contexts) == 0:
                try:
                    self.session.ui.emit('status', {'message': 'No contexts to clear.'})
                except Exception:
                    pass
                return True
            lines = []
            for idx, context in enumerate(contexts):
                lines.append(f"[{idx}] {context['context'].get()['name']}")
                lines.append(f"Content: {context['context'].get()['content']}")
            lines.append("")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

        if args[0] == 'usage':
            usage = self.session.get_provider().get_usage()
            lines = [f"{key}: {value}" for key, value in usage.items()]
            lines.append("")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

        if args[0] == 'cost':
            cost = self.session.get_provider().get_cost()
            if not cost:
                try:
                    self.session.ui.emit('status', {'message': 'Cost calculation not available for this model'})
                except Exception:
                    pass
                return

            input_cost = cost.get('input_cost', 0)
            output_cost = cost.get('output_cost', 0)

            lines = [f"input_cost: ${input_cost:.4f}", f"output_cost: ${output_cost:.4f}"]

            if input_cost == 0 and output_cost == 0:
                try:
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
                return

            for key, value in cost.items():
                if key not in ['input_cost', 'output_cost']:
                    lines.append(f"{key}: ${value:.4f}")
            lines.append("")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass
