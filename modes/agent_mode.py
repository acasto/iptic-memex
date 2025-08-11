from __future__ import annotations

from base_classes import InteractionMode
from actions.assistant_output_action import AssistantOutputAction


class AgentMode(InteractionMode):
    """
    Non-interactive N-turn loop that executes assistant turns with optional tool use
    between turns. Stops on reaching max steps or sentinel tokens.
    """

    def __init__(self, session, steps: int = 1, writes_policy: str = "deny", use_status_tags: bool = True):
        self.session = session
        self.steps = max(1, int(steps or 1))
        self.use_status_tags = bool(use_status_tags)

        # Seed agent write policy for file tools
        self.session.user_data["agent_mode"] = True
        self.session.user_data["agent_write_policy"] = (writes_policy or "deny").lower()
        # Also expose via params so actions can stay config/params-driven
        try:
            self.session.set_option('agent_mode', True)
            self.session.set_option('agent_write_policy', (writes_policy or "deny").lower())
            # Pull optional AGENT defaults for display behavior
            show_details = self.session.get_option('AGENT', 'show_context_details', fallback=None)
            if show_details is not None:
                self.session.set_option('show_context_details', show_details)
            detail_max = self.session.get_option('AGENT', 'context_detail_max_chars', fallback=None)
            if detail_max is not None:
                self.session.set_option('context_detail_max_chars', detail_max)
        except Exception:
            pass

        # Ensure a chat context exists
        if not self.session.get_context('chat'):
            self.session.add_context('chat')

        # Utilities
        self.utils = self.session.utils

    def _prepare_user_turn_with_contexts(self, turn_index: int):
        """Gather current contexts (including agent status) and add a new user turn."""
        # Build agent status as a lightweight context
        status_parts = [f"Turn {turn_index + 1} of {self.steps}"]
        if turn_index == self.steps - 1:
            status_parts.append("Final turn — include %%DONE%% if complete.")

        policy = self.session.user_data.get('agent_write_policy', 'deny')
        if policy == 'deny':
            status_parts.append("Writes are disabled; output a unified diff instead of writing.")
        elif policy == 'dry-run':
            status_parts.append("Dry run; output the diff you would apply.")

        status_text = ''.join(f"<status>{t}</status>" for t in status_parts if t)
        if status_text:
            self.session.add_context('agent', {
                'name': 'agent_status',
                'content': status_text
            })

        # Collect contexts for this turn and attach to a blank user message
        process_contexts = self.session.get_action('process_contexts')
        contexts = process_contexts.process_contexts_for_user(auto_submit=True) if process_contexts else []
        chat = self.session.get_context('chat')
        chat.add('', 'user', contexts)

        # Clear temporary contexts after attaching
        for context_type in list(self.session.context.keys()):
            if context_type not in ('prompt', 'chat'):
                self.session.remove_context_type(context_type)

    def _inject_status_tags(self, turn_index: int):
        if not self.use_status_tags:
            return
        final = (turn_index == self.steps - 1)

        tags = [f"Turn {turn_index + 1} of {self.steps}"]
        if final:
            tags.append("Final turn — include %%DONE%% if complete.")

        policy = self.session.user_data.get('agent_write_policy', 'deny')
        if policy == 'deny':
            tags.append("Writes are disabled; output a unified diff instead of writing.")
        elif policy == 'dry-run':
            tags.append("Dry run; output the diff you would apply.")

        status_line = ''.join(f"<status>{t}</status>" for t in tags if t)
        if status_line:
            self.session.get_context('chat').add(status_line, 'user')

    def _assistant_turn(self):
        """Produce one assistant response, handle output display and return raw and sanitized text."""
        params = self.session.get_params()
        provider = self.session.get_provider()
        output_processor = None

        response_text = None
        sanitized_for_tools = None

        # Label for readability (reuse chat colors)
        response_label = self.utils.output.style_text(
            params.get('response_label', 'Assistant:'),
            fg=params.get('response_label_color', 'green')
        )
        self.utils.output.write(f"{response_label} ", end='', flush=True)

        if params.get('stream', False):
            stream = provider.stream_chat()
            if not stream:
                self.utils.output.write("")
                return None, None
            output_processor = self.session.get_action('assistant_output')
            if output_processor:
                response_text = output_processor.run(stream, spinner_message="")
                # Prefer sanitized output for tool parsing
                sanitized_for_tools = output_processor.get_sanitized_output() or response_text
            else:
                # Fallback: manual stream
                response_text = ""
                for chunk in stream:
                    print(chunk, end='', flush=True)
                    response_text += chunk
                print()
                sanitized_for_tools = response_text
        else:
            response_text = provider.chat()
            if response_text is None:
                self.utils.output.write("")
                return None, None
            # Apply display-side filters for parity with streaming UX
            filtered_for_display = AssistantOutputAction.filter_full_text(response_text, self.session)
            self.utils.output.write(filtered_for_display)
            self.utils.output.write('')
            sanitized_for_tools = AssistantOutputAction.filter_full_text_for_return(response_text, self.session)

        return response_text, sanitized_for_tools

    def start(self):
        chat = self.session.get_context('chat')
        if not chat:
            self.utils.output.error("AgentStepsMode: chat context not available")
            return

        provider = self.session.get_provider()
        if not provider:
            self.utils.output.error("AgentStepsMode: no provider available")
            return

        commands_action = self.session.get_action('assistant_commands')

        # N-turn loop
        for i in range(self.steps):
            final = (i == self.steps - 1)

            # Prepare per-turn user message from current contexts (tools results, status, etc.)
            self._prepare_user_turn_with_contexts(i)

            # Get assistant output (with streaming if configured)
            response, sanitized = self._assistant_turn()
            if not response:
                self.utils.output.debug(f"[Agent] Empty response on turn {i+1}; stopping.")
                break

            # Record assistant message
            chat.add(response, 'assistant')

            # Sentinel-based early exit
            if ('%%DONE%%' in response) or ('%%COMPLETE%%' in response):
                self.utils.output.debug(f"[Agent] Sentinel detected on turn {i+1}; stopping.")
                break

            # Tool execution based on assistant output
            ran_tools = False
            if commands_action and sanitized is not None:
                try:
                    # Heuristic: if there are no parsed commands, optionally early exit
                    parsed = commands_action.parse_commands(sanitized)
                    if parsed:
                        ran_tools = True
                        commands_action.run(sanitized)
                except Exception as e:
                    # Bubble errors into assistant context for next turn
                    self.session.add_context('assistant', {
                        'name': 'command_error',
                        'content': f'Error running assistant commands: {e}'
                    })

            # Optional early exit heuristic: if no tools and not final
            if not final and not ran_tools:
                self.utils.output.debug(f"[Agent] No tools invoked on turn {i+1}; stopping early.")
                break

        # Final newline separation
        self.utils.output.write('')
