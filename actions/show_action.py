from session_handler import InteractionAction


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
            print("Params:")
            print("---------------------------------")
            for key, value in sorted_params:
                if key == 'api_key':
                    value = '********'
                print(f"{key}: {value}")
            print()

        if args[0] == 'tool-settings':
            tools = self.session.get_tools()
            sorted_tools = sorted(tools.items())
            print("Tools:")
            print("---------------------------------")
            for key, value in sorted_tools:
                print(f"{key}: {value}")
            print()

        if args[0] == 'models':
            for section, options in self.session.list_models().items():
                print(section)
            print()

        if args[0] == 'messages':
            try:
                # First, show the system prompt if it exists
                prompt_context = self.session.get_context('prompt')
                if prompt_context:
                    prompt_data = prompt_context.get()
                    if prompt_data and prompt_data.get('content'):
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
                        print("No chat context available")
                        return
                else:
                    # Get messages from provider (which delegates to chat context)
                    provider = self.session.get_provider()
                    if provider:
                        messages = provider.get_messages()
                    else:
                        print("No provider available")
                        return
                
                if not messages:
                    print("No conversation messages to display")
                    return
                
                # Display messages with proper formatting
                print("=== CONVERSATION ===")
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
                    
                    print(f"[{i}] {timestamp} - {role.upper()}")
                    if content:
                        print(f"    Message: {content}")
                    else:
                        print("    Message: (empty)")
                    
                    if context:
                        print(f"    Context: {len(context)} item(s)")
                        for j, ctx in enumerate(context):
                            ctx_type = ctx.get('type', 'unknown')
                            print(f"      [{j}] Type: {ctx_type}")
                    
                    print()
                    
            except Exception as e:
                print(f"Error displaying messages: {e}")
                print()

        if args[0] == 'contexts':
            contexts = self.session.get_action('process_contexts').get_contexts(self.session)
            if len(contexts) == 0:
                print(f"No contexts to clear.\n")
                return True

            for idx, context in enumerate(contexts):
                print(f"[{idx}] {context['context'].get()['name']}")
                print(f"Content: {context['context'].get()['content']}")
            print()

        if args[0] == 'usage':
            usage = self.session.get_provider().get_usage()
            for key, value in usage.items():
                print(f"{key}: {value}")
            print()

        if args[0] == 'cost':
            cost = self.session.get_provider().get_cost()
            if not cost:
                print("Cost calculation not available for this model")
                return

            input_cost = cost.get('input_cost', 0)
            output_cost = cost.get('output_cost', 0)

            print(f"input_cost: ${input_cost:.4f}")
            print(f"output_cost: ${output_cost:.4f}")

            if input_cost == 0 and output_cost == 0:
                print()
                return

            for key, value in cost.items():
                if key not in ['input_cost', 'output_cost']:
                    print(f"{key}: ${value:.4f}")
            print()