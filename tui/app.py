"""
Main Textual App for iptic-memex TUI mode.

This is the core Textual application that provides the terminal user interface.
It integrates with the Session architecture to provide all functionality.
"""

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Input, RichLog, Static, Footer
    from textual.binding import Binding
    from rich.text import Text
    from rich.markdown import Markdown
    import asyncio
    TEXTUAL_AVAILABLE = True
except ImportError:
    # Handle gracefully if textual is not installed
    App = object
    ComposeResult = None
    Binding = None
    TEXTUAL_AVAILABLE = False


if TEXTUAL_AVAILABLE:
    class MemexTUIApp(App):
        """
        Main Textual application for iptic-memex TUI mode.
        
        Provides a rich terminal interface for chat, context management,
        and all iptic-memex functionality while leveraging the Session
        architecture for business logic.
        """
        
        CSS = """
        #chat_log {
            height: 1fr;
            border: solid $primary;
            margin: 1;
        }
        
        #input_area {
            height: 3;
            border: solid $primary;
            margin: 1;
        }
        
        #status_bar {
            height: 1;
            background: $primary;
            color: $text;
            content-align: center middle;
        }
        """
        
        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit"),
            Binding("ctrl+q", "quit", "Quit"),
            Binding("enter", "submit_message", "Send"),
            Binding("escape", "clear_input", "Clear"),
        ]
        
        def __init__(self, session, builder=None):
            """
            Initialize the Textual app with session.
            
            Args:
                session: The Session object with all business logic
                builder: SessionBuilder for model switching, etc.
            """
            super().__init__()
            self.session = session
            self.builder = builder
            self.chat_context = session.get_context('chat')
            
        def compose(self) -> ComposeResult:
            """Create the UI layout."""
            # Status bar showing current model and basic info
            params = self.session.get_params()
            model_name = params.get('model', 'unknown')
            status_text = f"iptic-memex TUI | Model: {model_name} | Press Ctrl+C to quit"
            
            yield Static(status_text, id="status_bar")
            
            # Main chat area
            yield RichLog(id="chat_log", markup=True)
            
            # Input area
            yield Input(
                placeholder="Type your message here... (Press Enter to send)",
                id="input_area"
            )
            
            # Footer with key bindings
            yield Footer()
        
        def on_mount(self) -> None:
            """Called when the app is mounted."""
            self.title = "iptic-memex TUI"
            
            # Focus the input area
            self.query_one("#input_area").focus()
            
            # Display welcome message
            chat_log = self.query_one("#chat_log", RichLog)
            chat_log.write("Welcome to iptic-memex TUI mode!")
            chat_log.write("Type your message below and press Enter to send.")
            chat_log.write("")
            
            # Show any existing chat history
            self._display_chat_history()
        
        def _display_chat_history(self):
            """Display existing chat history if any."""
            if self.chat_context and hasattr(self.chat_context, 'get'):
                chat_log = self.query_one("#chat_log", RichLog)
                
                try:
                    messages = self.chat_context.get()
                    if messages:
                        for message in messages:
                            role = message.get('role', 'unknown')
                            content = message.get('message', '')  # Note: it's 'message', not 'content'
                            
                            if role == 'user':
                                chat_log.write(f"[bold blue]You:[/bold blue] {content}")
                            elif role == 'assistant':
                                chat_log.write(f"[bold green]Assistant:[/bold green] {content}")
                            else:
                                chat_log.write(f"[dim]{role}:[/dim] {content}")
                        
                        if messages:
                            chat_log.write("")
                except Exception as e:
                    chat_log.write(f"[dim]Error loading chat history: {e}[/dim]")
        
        async def on_input_submitted(self, event: Input.Submitted) -> None:
            """Handle when user submits input."""
            message = event.value.strip()
            if not message:
                return
                
            # Clear the input
            event.input.value = ""
            
            # Display user message
            chat_log = self.query_one("#chat_log", RichLog)
            chat_log.write(f"[bold blue]You:[/bold blue] {message}")
            
            # Check if it's a command first
            user_commands = self.session.get_action('chat_commands')
            if user_commands and user_commands.run(message):
                # Command was handled
                chat_log.write("[dim]Command executed.[/dim]")
                chat_log.write("")
                return
            
            # Ensure we have a chat context
            if not self.chat_context:
                chat_log.write("[red]Error: No chat context available[/red]")
                return
            
            # Add message to chat context
            if hasattr(self.chat_context, 'add'):
                # Get any active contexts for the message
                contexts = self._get_active_contexts()
                try:
                    self.chat_context.add(message, 'user', contexts)
                    chat_log.write("[dim]Message added to chat context[/dim]")
                except Exception as e:
                    chat_log.write(f"[red]Error adding message to context: {e}[/red]")
                    return
            else:
                chat_log.write("[red]Error: Chat context doesn't support adding messages[/red]")
                return
            
            # Clear temporary contexts (like regular ChatMode does)
            for context_type in list(self.session.context.keys()):
                if context_type not in ('prompt', 'chat'):
                    self.session.remove_context_type(context_type)
            
            # Get response from provider
            await self._get_assistant_response()
        
        def _get_active_contexts(self):
            """Get active contexts for the current message."""
            contexts = []
            for context_type, context_list in self.session.context.items():
                if context_type not in ('prompt', 'chat'):
                    for context in context_list:
                        contexts.append({
                            'type': context_type,
                            'context': context
                        })
            return contexts
        
        async def _get_assistant_response(self):
            """Get response from the assistant."""
            chat_log = self.query_one("#chat_log", RichLog)
            
            try:
                chat_log.write("[dim]Debug: Starting _get_assistant_response[/dim]")
                
                provider = self.session.get_provider()
                chat_log.write(f"[dim]Debug: Provider type: {type(provider)}[/dim]")
                
                if not provider:
                    chat_log.write("[red]Error: No provider available[/red]")
                    chat_log.write("[dim]Install a provider (e.g., pip install openai) and configure API keys[/dim]")
                    return
                
                # Ensure we have a chat context with messages
                if not self.chat_context:
                    chat_log.write("[red]Error: No chat context available[/red]")
                    return
                
                # Check if chat context has messages using the correct interface
                try:
                    messages = self.chat_context.get()
                    chat_log.write(f"[dim]Debug: Found {len(messages)} messages in context[/dim]")
                    if not messages:
                        chat_log.write("[red]Error: No messages in chat context[/red]")
                        return
                except Exception as e:
                    chat_log.write(f"[red]Error accessing chat messages: {e}[/red]")
                    return
                
                params = self.session.get_params()
                chat_log.write(f"[dim]Debug: Model={params.get('model')}, Stream={params.get('stream')}[/dim]")
                
                if params.get('stream', False):
                    # Handle streaming response
                    chat_log.write("[bold green]Assistant:[/bold green] [dim]Thinking...[/dim]")
                    await self._handle_streaming_response(provider, chat_log)
                else:
                    # Handle non-streaming response
                    chat_log.write("[bold green]Assistant:[/bold green] [dim]Thinking...[/dim]")
                    chat_log.write("[dim]Debug: About to call provider.chat()[/dim]")
                    
                    response = provider.chat()
                    
                    chat_log.write("[dim]Debug: provider.chat() completed successfully[/dim]")
                    if response:
                        # Remove the "thinking" message by rewriting the line
                        chat_log.write(f"[bold green]Assistant:[/bold green] {response}")
                        
                        # Add to chat context
                        if self.chat_context and hasattr(self.chat_context, 'add'):
                            self.chat_context.add(response, 'assistant')
                            chat_log.write("[dim]Debug: Response added to chat context[/dim]")
                    else:
                        chat_log.write("[red]No response received[/red]")
                
                chat_log.write("")
                
            except Exception as e:
                chat_log.write(f"[red]Error: {e}[/red]")
                chat_log.write("")
                # Add detailed debugging info
                chat_log.write(f"[dim]Debug: Error type: {type(e)}[/dim]")
                chat_log.write(f"[dim]Debug: Chat context type: {type(self.chat_context)}[/dim]")
                if self.chat_context and hasattr(self.chat_context, 'get'):
                    try:
                        messages = self.chat_context.get()
                        chat_log.write(f"[dim]Debug: Messages count: {len(messages)}[/dim]")
                        if messages:
                            chat_log.write(f"[dim]Debug: Last message: {messages[-1]}[/dim]")
                    except Exception as debug_e:
                        chat_log.write(f"[dim]Debug: Error getting messages: {debug_e}[/dim]")
                
                # Print full traceback to help debug
                import traceback
                tb_lines = traceback.format_exc().split('\n')
                for line in tb_lines[-10:]:  # Show last 10 lines of traceback
                    if line.strip():
                        chat_log.write(f"[dim]{line}[/dim]")
        
        async def _handle_streaming_response(self, provider, chat_log):
            """Handle streaming response from provider."""
            try:
                if hasattr(provider, 'stream_chat'):
                    stream = provider.stream_chat()
                    if stream:
                        # Clear the "thinking" message and start fresh
                        chat_log.clear()
                        self._display_chat_history()
                        
                        # Start the assistant message
                        response_text = ""
                        assistant_prefix = "[bold green]Assistant:[/bold green] "
                        
                        # Stream the response
                        for chunk in stream:
                            if chunk:
                                response_text += chunk
                                # Clear and rewrite the full response each time
                                # This isn't the most efficient but works for now
                                chat_log.clear()
                                self._display_chat_history()
                                chat_log.write(f"{assistant_prefix}{response_text}")
                        
                        # Add final response to chat context
                        if self.chat_context and hasattr(self.chat_context, 'add'):
                            self.chat_context.add(response_text, 'assistant')
                    else:
                        chat_log.write("[red]No stream available[/red]")
                else:
                    chat_log.write("[red]Streaming not supported by provider[/red]")
                    
            except Exception as e:
                chat_log.write(f"[red]Streaming error: {e}[/red]")
        
        def action_submit_message(self) -> None:
            """Action to submit the current message."""
            input_widget = self.query_one("#input_area", Input)
            # Trigger the submitted event
            self.call_from_thread(input_widget.action_submit)
        
        def action_clear_input(self) -> None:
            """Action to clear the input area."""
            input_widget = self.query_one("#input_area", Input)
            input_widget.value = ""
        
        def action_quit(self) -> None:
            """Action to quit the application."""
            self.exit()

else:
    # Fallback if Textual is not available
    class MemexTUIApp:
        def __init__(self, session, builder=None):
            pass
        
        def run(self):
            print("Error: Textual library not installed.")
            print("Install with: pip install textual")
