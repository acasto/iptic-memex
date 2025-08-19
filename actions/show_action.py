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
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try:
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
            else:
                for ln in lines:
                    print(ln)

        if args[0] == 'tool-settings':
            tools = self.session.get_tools()
            sorted_tools = sorted(tools.items())
            lines = ["Tools:", "---------------------------------"]
            for key, value in sorted_tools:
                lines.append(f"{key}: {value}")
            lines.append("")
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try:
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
            else:
                for ln in lines:
                    print(ln)

        if args[0] == 'models':
            sections = list(self.session.list_models().keys())
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try:
                    self.session.ui.emit('status', {'message': "\n".join(sections)})
                except Exception:
                    pass
            else:
                for s in sections:
                    print(s)
                print()

        if args[0] == 'messages':
            try:
                # First, show the system prompt if it exists
                prompt_context = self.session.get_context('prompt')
                if prompt_context:
                    prompt_data = prompt_context.get()
                    if prompt_data and prompt_data.get('content'):
                        if not getattr(self.session.ui.capabilities, 'blocking', False):
                            try:
                                self.session.ui.emit('status', {'message': '=== SYSTEM PROMPT ===\n' + prompt_data['content'] + '\n' + ('='*50) + '\n'})
                            except Exception:
                                pass
                        else:
                            print("=== SYSTEM PROMPT ===")
                            print(prompt_data['content'])
                            print("=" * 50)
                            print()

                # Then show the conversation messages
                if len(args) > 1 and args[1] == 'all':
                    # Get all messages from chat context
                    chat_context = self.session.get_context('chat')
                    if chat_context:
                        messages = chat_context.get("all")
                    else:
                        if not getattr(self.session.ui.capabilities, 'blocking', False):
                            try: self.session.ui.emit('status', {'message': 'No chat context available'})
                            except Exception: pass
                        else:
                            print("No chat context available")
                        return
                else:
                    # Get messages from provider (which delegates to chat context)
                    provider = self.session.get_provider()
                    if provider:
                        messages = provider.get_messages()
                    else:
                        if not getattr(self.session.ui.capabilities, 'blocking', False):
                            try: self.session.ui.emit('status', {'message': 'No provider available'})
                            except Exception: pass
                        else:
                            print("No provider available")
                        return
                
                if not messages:
                    print("No conversation messages to display")
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
                if not getattr(self.session.ui.capabilities, 'blocking', False):
                    try: self.session.ui.emit('error', {'message': f'Error displaying messages: {e}'})
                    except Exception: pass
                else:
                    print(f"Error displaying messages: {e}")
                    print()
                return

            # Emit or print assembled lines
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try:
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
            else:
                for ln in lines:
                    print(ln)

        if args[0] == 'contexts':
            contexts = self.session.get_action('process_contexts').get_contexts(self.session)
            if len(contexts) == 0:
                if not getattr(self.session.ui.capabilities, 'blocking', False):
                    try: self.session.ui.emit('status', {'message': 'No contexts to clear.'})
                    except Exception: pass
                else:
                    print(f"No contexts to clear.\n")
                return True
            lines = []
            for idx, context in enumerate(contexts):
                lines.append(f"[{idx}] {context['context'].get()['name']}")
                lines.append(f"Content: {context['context'].get()['content']}")
            lines.append("")
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try: self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception: pass
            else:
                for ln in lines: print(ln)

        if args[0] == 'usage':
            usage = self.session.get_provider().get_usage()
            lines = [f"{key}: {value}" for key, value in usage.items()]
            lines.append("")
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try: self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception: pass
            else:
                for ln in lines: print(ln)

        if args[0] == 'cost':
            cost = self.session.get_provider().get_cost()
            if not cost:
                if not getattr(self.session.ui.capabilities, 'blocking', False):
                    try: self.session.ui.emit('status', {'message': 'Cost calculation not available for this model'})
                    except Exception: pass
                else:
                    print("Cost calculation not available for this model")
                return

            input_cost = cost.get('input_cost', 0)
            output_cost = cost.get('output_cost', 0)

            lines = [f"input_cost: ${input_cost:.4f}", f"output_cost: ${output_cost:.4f}"]

            if input_cost == 0 and output_cost == 0:
                if not getattr(self.session.ui.capabilities, 'blocking', False):
                    try: self.session.ui.emit('status', {'message': "\n".join(lines)})
                    except Exception: pass
                else:
                    for ln in lines: print(ln)
                    print()
                return

            for key, value in cost.items():
                if key not in ['input_cost', 'output_cost']:
                    lines.append(f"{key}: ${value:.4f}")
            lines.append("")
            if not getattr(self.session.ui.capabilities, 'blocking', False):
                try: self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception: pass
            else:
                for ln in lines: print(ln)
