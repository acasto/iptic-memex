from __future__ import annotations

from base_classes import InteractionMode
from actions.assistant_output_action import AssistantOutputAction


class AgentMode(InteractionMode):
    """
    Non-interactive N-turn loop that executes assistant turns with optional tool use
    between turns. Stops on reaching max steps or sentinel tokens.
    """

    def __init__(self, session, steps: int = 1, writes_policy: str = "deny", use_status_tags: bool = True, output_mode: str | None = None):
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
            # Output mode: CLI overrides config; fallback to [AGENT].output or 'final'
            cfg_output = self.session.get_option('AGENT', 'output', fallback='final')
            mode = (output_mode or cfg_output or 'final').lower()
            if mode not in ('final', 'full', 'none'):
                mode = 'final'
            self.session.set_option('agent_output_mode', mode)
            # In Agent mode, only show context summaries/details when output is 'full'
            self.session.set_option('show_context_summary', mode == 'full')
            self.session.set_option('show_context_details', mode == 'full')
        except Exception:
            pass

        # Ensure a chat context exists
        if not self.session.get_context('chat'):
            self.session.add_context('chat')

        # Utilities
        self.utils = self.session.utils

    def _dump_messages(self, header: str, include_prompt: bool = False, omit_last_assistant: bool = False):
        """Debug helper: dump system prompt and conversation messages."""
        try:
            out = self.utils.output
            out.write(f"=== {header} ===")

            # System prompt (optional; print once at start)
            if include_prompt:
                prompt_context = self.session.get_context('prompt')
                if prompt_context:
                    prompt_data = prompt_context.get()
                    content = prompt_data.get('content') if isinstance(prompt_data, dict) else None
                    if content:
                        out.write("--- SYSTEM PROMPT ---")
                        out.write(str(content))

            # Conversation messages (prefer provider view)
            provider = self.session.get_provider()
            messages = []
            if provider and hasattr(provider, 'get_messages'):
                try:
                    messages = provider.get_messages() or []
                except Exception:
                    messages = []
            if not messages:
                chat = self.session.get_context('chat')
                messages = chat.get("all") if chat else []
            out.write("--- MESSAGES ---")

            # Optionally omit the most recent assistant message (e.g., when already streamed)
            omit_idx = -1
            if omit_last_assistant:
                for idx in range(len(messages) - 1, -1, -1):
                    if messages[idx].get('role') == 'assistant':
                        omit_idx = idx
                        break

            for i, message in enumerate(messages):
                role = message.get('role', 'unknown')
                # Skip system messages here to avoid re-printing the prompt on every turn
                if role == 'system':
                    continue
                if i == omit_idx:
                    # Keep the role header but omit the content to avoid duplication
                    out.write(f"[{i}] {role.upper()}:")
                    continue
                # Extract text for modern content or legacy 'message'
                text = ''
                if 'message' in message and message['message']:
                    text = message['message']
                elif 'content' in message:
                    data = message['content']
                    if isinstance(data, list):
                        text_parts = []
                        for item in data:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                text_parts.append(item.get('text', ''))
                        text = ' '.join(text_parts)
                    elif isinstance(data, str):
                        text = data
                out.write(f"[{i}] {role.upper()}: {text}")
        except Exception:
            # Debug dump should never break the agent loop
            pass

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

        # Collect contexts for this turn and attach to a user message
        process_contexts = self.session.get_action('process_contexts')
        contexts = process_contexts.process_contexts_for_user(auto_submit=True) if process_contexts else []

        # If stdin was provided via -f -, extract its content as the actual user message
        stdin_content = None
        stdin_idx = None
        for idx, c in enumerate(contexts):
            try:
                meta = c['context'].get()
                if meta and meta.get('name') == 'stdin':
                    stdin_content = meta.get('content')
                    stdin_idx = idx
                    break
            except Exception:
                continue

        if stdin_idx is not None:
            # Remove stdin from contexts so it isn't rendered as a file context
            contexts.pop(stdin_idx)
            # If prompt wasn't explicitly set, mirror completion mode by removing default prompt
            try:
                if 'prompt' not in getattr(self.session.config, 'overrides', {}):
                    self.session.remove_context_type('prompt')
            except Exception:
                pass

        chat = self.session.get_context('chat')
        chat.add(stdin_content or '', 'user', contexts)

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
        """Produce one assistant response, handle output per agent_output_mode, and return raw and sanitized text."""
        params = self.session.get_params()
        provider = self.session.get_provider()
        output_processor = None

        response_text = None
        sanitized_for_tools = None

        output_mode = params.get('agent_output_mode', 'final')
        show_stream = (output_mode == 'full')
        if show_stream:
            # Label for readability (reuse chat colors)
            response_label = self.utils.output.style_text(
                params.get('response_label', 'Assistant:'),
                fg=params.get('response_label_color', 'green')
            )
            self.utils.output.write(f"{response_label} ", end='', flush=True)

        if params.get('stream', False):
            stream = provider.stream_chat()
            if not stream:
                if show_stream:
                    self.utils.output.write("")
                return None, None
            if show_stream:
                # Use output action to preserve filter behavior for display
                output_processor = self.session.get_action('assistant_output')
                if output_processor:
                    response_text = output_processor.run(stream, spinner_message="")
                    sanitized_for_tools = output_processor.get_sanitized_output() or response_text
                else:
                    response_text = ""
                    for chunk in stream:
                        self.utils.output.write(chunk, end='', flush=True)
                        response_text += chunk
                    self.utils.output.write('')
                    sanitized_for_tools = response_text
            else:
                # Silent accumulation of full response
                parts = []
                for chunk in stream:
                    if isinstance(chunk, str):
                        parts.append(chunk)
                response_text = ''.join(parts)
                sanitized_for_tools = AssistantOutputAction.filter_full_text_for_return(response_text, self.session)
        else:
            response_text = provider.chat()
            if response_text is None:
                if show_stream:
                    self.utils.output.write("")
                return None, None
            # Apply display-side filters for parity with streaming UX
            if show_stream:
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

        out_mode = (self.session.get_params().get('agent_output_mode', 'final') or 'final').lower()

        # In final/none, suppress leading blank spacing and newline bursts from chat-mode flows.
        suppress_ctx = self.utils.output.suppress_stdout_blanks(suppress_blank_lines=True, collapse_bursts=True) \
            if out_mode in ('final', 'none') else None

        with (suppress_ctx if suppress_ctx is not None else self.utils.output.suppress_stdout_blanks(False, False)):
            # N-turn loop
            last_assistant_display = None
            debug_dump = bool(self.session.get_params().get('agent_debug', False))
            for i in range(self.steps):
                final = (i == self.steps - 1)

                # Prepare per-turn user message from current contexts (tools results, status, etc.)
                self._prepare_user_turn_with_contexts(i)

                if debug_dump:
                    # Include system prompt only at the first turn
                    # Omit the last assistant message in dumps to avoid duplication
                    self._dump_messages(f"BEFORE TURN {i+1}", include_prompt=(i == 0), omit_last_assistant=True)

                # Get assistant output (with streaming if configured)
                response, sanitized = self._assistant_turn()
                if not response:
                    self.utils.output.debug(f"[Agent] Empty response on turn {i+1}; stopping.")
                    break

                # Record assistant message
                chat.add(response, 'assistant')
                # Keep latest display-friendly version for final/none handling
                last_assistant_display = AssistantOutputAction.filter_full_text(response, self.session)

                # Suppress AFTER dump for the final turn to avoid duplicating the final answer
                # If needed later, we can re-enable for non-final turns only
                if debug_dump and not final:
                    # Always omit the most recent assistant to avoid duplication in logs
                    self._dump_messages(f"AFTER TURN {i+1}", omit_last_assistant=True)

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

            # No trailing spacer here; avoid introducing a blank before final output

        # Output policy after the loop
        if out_mode == 'final':
            # If raw mode requested, emit only the raw response (programmatic use)
            if self.session.get_params().get('raw_completion', False):
                provider = self.session.get_provider()
                if provider and hasattr(provider, 'get_full_response'):
                    raw = provider.get_full_response()
                    try:
                        import json
                        raw_str = json.dumps(raw, indent=2, ensure_ascii=False) if not isinstance(raw, str) else raw
                    except Exception:
                        raw_str = str(raw)
                    self.utils.output.write(raw_str, end='')
            elif last_assistant_display:
                final_text = last_assistant_display
                # Trim a single leading newline (CRLF or LF) that some providers produce
                if isinstance(final_text, str):
                    if final_text.startswith('\r\n'):
                        final_text = final_text[2:]
                    elif final_text.startswith('\n'):
                        final_text = final_text[1:]
                self.utils.output.write(final_text)
        # 'none': no assistant output here; 'full': already streamed
