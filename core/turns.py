from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple, Literal


@dataclass
class TurnOptions:
    """Options that influence turn orchestration."""

    stream: Optional[bool] = None
    agent_output_mode: Optional[Literal['final', 'full', 'none']] = None
    verbose_dump: bool = False
    allow_auto_submit: Optional[bool] = None
    sentinels: List[str] = field(
        default_factory=lambda: ["%%DONE%%", "%%COMPLETED%%", "%%COMPLETE%%"]
    )
    early_stop_no_tools: bool = False
    # When True, do not print context summaries/details during the turn; caller
    # is expected to have shown pre-prompt updates already.
    suppress_context_print: bool = False


@dataclass
class TurnResult:
    """Outcome summary for one or more assistant turns."""

    last_text: Optional[str]
    sanitized: Optional[str]
    turns_executed: int
    stopped_on_sentinel: bool
    ran_tools: bool


class TurnRunner:
    """Unified orchestration for user/assistant turns and tool execution.

    Moved to core to make it reusable across CLI/Web/TUI and internal runs.
    """

    def __init__(self, session) -> None:
        self.session = session
        self.utils = session.utils

    # ---- Public API ----------------------------------------------------
    def run_user_turn(self, input_text: str, *, options: Optional[TurnOptions] = None) -> TurnResult:
        opts = options or TurnOptions()
        params = self.session.get_params() or {}
        stream = bool(opts.stream if opts.stream is not None else params.get("stream", False))
        allow_auto_submit = (
            bool(opts.allow_auto_submit)
            if opts.allow_auto_submit is not None
            else bool(self.session.get_option("TOOLS", "allow_auto_submit", fallback=False))
        )

        turns_executed = 0
        last_display: Optional[str] = None
        last_sanitized: Optional[str] = None
        stopped_on_sentinel = False
        any_tools = False

        # Reset per-turn cancellation flag
        try:
            self.session.set_flag('turn_cancelled', False)
        except Exception:
            pass
        # Initialize a per-turn cancellation token
        token = None
        try:
            from core.cancellation import CancellationToken  # local import to avoid import cycles
            token = CancellationToken()
            self.session.set_user_data('__turn_cancel__', token)
        except Exception:
            token = None

        initial_auto = bool(self.session.get_flag("auto_submit"))
        contexts = self._process_contexts(auto_submit=initial_auto, suppress_print=opts.suppress_context_print)
        if initial_auto:
            self.session.set_flag("auto_submit", False)
        self._add_user_message("" if initial_auto else (input_text or ""), contexts)
        self._clear_temp_contexts()

        # Limit assistant follow-ups (auto-submit loops). Configurable via [TOOLS].auto_submit_max_turns.
        try:
            safety_cap = int(self.session.get_option("TOOLS", "auto_submit_max_turns", fallback=6))
        except Exception:
            safety_cap = 6
        if safety_cap <= 0:
            safety_cap = 1
        for _ in range(safety_cap):
            raw, display, sanitized = self._assistant_turn(stream=stream, output_mode=None)
            turns_executed += 1
            last_display = display
            last_sanitized = sanitized
            self._record_assistant(raw)
            # If the turn was cancelled mid-stream, stop without executing tools
            try:
                if self.session.get_flag('turn_cancelled'):
                    break
            except Exception:
                pass
            try:
                if token and token.is_cancelled():
                    break
            except Exception:
                pass
            if display and self._contains_sentinel(display, opts.sentinels):
                stopped_on_sentinel = True
                break
            ran = self._execute_tools(sanitized or display or raw or "")
            any_tools = any_tools or ran
            if allow_auto_submit and self.session.get_flag("auto_submit"):
                contexts2 = self._process_contexts(auto_submit=True, suppress_print=opts.suppress_context_print)
                if self.session.get_flag("auto_submit"):
                    self.session.set_flag("auto_submit", False)
                    self._add_user_message("", contexts2)
                    self._clear_temp_contexts()
                    continue
            break

        return TurnResult(
            last_text=self._strip_sentinels(last_display, opts.sentinels) if last_display else last_display,
            sanitized=last_sanitized,
            turns_executed=turns_executed,
            stopped_on_sentinel=stopped_on_sentinel,
            ran_tools=any_tools,
        )

    def run_agent_loop(
        self,
        steps: int,
        *,
        prepare_prompt: Optional[Callable[[Any, int, int, bool], None]] = None,
        options: Optional[TurnOptions] = None,
    ) -> TurnResult:
        opts = options or TurnOptions()
        params = self.session.get_params() or {}
        output_mode = (opts.agent_output_mode or params.get("agent_output_mode") or "final").lower()
        stream = (output_mode == "full")

        total_turns = max(1, int(steps or 1))
        turns_executed = 0
        last_display: Optional[str] = None
        last_sanitized: Optional[str] = None
        stopped_on_sentinel = False
        any_tools = False

        # Reset per-run cancellation flag
        try:
            self.session.set_flag('turn_cancelled', False)
        except Exception:
            pass
        # Initialize a per-run cancellation token
        token = None
        try:
            from core.cancellation import CancellationToken
            token = CancellationToken()
            self.session.set_user_data('__turn_cancel__', token)
        except Exception:
            token = None

        if not self.session.get_context("chat"):
            self.session.add_context("chat")

        for i in range(total_turns):
            final_turn = (i == total_turns - 1)

            status_text = f"Turn {i + 1} of {total_turns}"
            try:
                self.session.add_context("agent", {"name": "agent_status", "content": f"<status>{status_text}</status>"})
            except Exception:
                pass

            contexts = self._process_contexts(auto_submit=True, suppress_print=(output_mode != "full"))

            stdin_content: Optional[str] = None
            stdin_idx: Optional[int] = None
            try:
                for idx, c in enumerate(contexts or []):
                    meta = c.get("context").get() if isinstance(c, dict) and c.get("context") else None
                    if meta and meta.get("name") == "stdin":
                        stdin_content = meta.get("content")
                        stdin_idx = idx
                        break
            except Exception:
                stdin_idx = None
            if stdin_idx is not None:
                try:
                    contexts.pop(stdin_idx)
                except Exception:
                    pass

            if i == 0 and prepare_prompt:
                try:
                    prepare_prompt(self.session, i + 1, total_turns, bool(stdin_idx is not None))
                except Exception:
                    pass

            self._add_user_message(stdin_content or "", contexts)
            self._clear_temp_contexts()

            if opts.verbose_dump:
                try:
                    self._dump_messages(
                        header=f"BEFORE TURN {i+1}",
                        include_prompt=(i == 0),
                        omit_last_assistant=True,
                    )
                except Exception:
                    pass

            raw, display, sanitized = self._assistant_turn(stream=stream, output_mode=output_mode)
            turns_executed += 1
            last_display = display
            last_sanitized = sanitized
            self._record_assistant(raw)

            text_for_stop = display or sanitized or raw or ""
            # If cancelled mid-stream, stop early and do not execute tools
            try:
                if self.session.get_flag('turn_cancelled'):
                    break
            except Exception:
                pass
            try:
                if token and token.is_cancelled():
                    break
            except Exception:
                pass
            if text_for_stop and self._contains_sentinel(text_for_stop, opts.sentinels):
                stopped_on_sentinel = True
                break

            ran = self._execute_tools(sanitized or display or raw or "")
            any_tools = any_tools or ran

            if (not final_turn) and opts.early_stop_no_tools and (not ran):
                break

        if last_display and output_mode == "final":
            last_display = self._strip_sentinels(last_display, opts.sentinels)

        return TurnResult(
            last_text=last_display,
            sanitized=last_sanitized,
            turns_executed=turns_executed,
            stopped_on_sentinel=stopped_on_sentinel,
            ran_tools=any_tools,
        )

    # ---- Helpers -------------------------------------------------------
    def _process_contexts(self, *, auto_submit: bool, suppress_print: bool) -> list:
        try:
            action = self.session.get_action("process_contexts")
            if not action:
                return []
            if suppress_print:
                return action.get_contexts(self.session)
            else:
                return action.process_contexts_for_user(auto_submit=auto_submit)
        except Exception:
            return []

    def _add_user_message(self, text: str, contexts: list) -> None:
        try:
            chat = self.session.get_context("chat")
            if chat is None:
                chat = self.session.add_context("chat")
            chat.add(text or "", "user", contexts or [])
        except Exception:
            pass

    def _clear_temp_contexts(self) -> None:
        try:
            for context_type in list(self.session.context.keys()):
                if context_type not in ("prompt", "chat"):
                    self.session.remove_context_type(context_type)
        except Exception:
            pass

    def _assistant_turn(self, *, stream: bool, output_mode: Optional[str]) -> Tuple[str, str, str]:
        provider = self.session.get_provider()
        if not provider:
            return "", "", ""

        if stream:
            out_action = self.session.get_action("assistant_output")
            try:
                stream_iter = provider.stream_chat()
            except Exception:
                stream_iter = None

            raw = ""
            if out_action and stream_iter is not None:
                try:
                    raw = out_action.run(stream_iter, spinner_message="") or ""
                except Exception:
                    raw = ""
                try:
                    display = (getattr(out_action, "get_display_output", None) or (lambda: None))() or raw
                except Exception:
                    display = raw
                try:
                    sanitized = (getattr(out_action, "get_sanitized_output", None) or (lambda: None))() or raw
                except Exception:
                    sanitized = raw
                return raw, str(display), str(sanitized)
            try:
                for chunk in (stream_iter or []):
                    self.utils.output.write(chunk, end="", flush=True)
                    raw += chunk
                self.utils.output.write("")
            except Exception:
                pass
            return raw, raw, raw

        orig_stream = None
        try:
            try:
                orig_stream = self.session.get_params().get('stream')
                if orig_stream:
                    self.session.set_option('stream', False)
            except Exception:
                pass
            raw_text = provider.chat()
        except Exception as e:
            raw_text = f"[error] {e}"
        finally:
            try:
                if orig_stream is not None:
                    self.session.set_option('stream', bool(orig_stream))
            except Exception:
                pass
        if raw_text is None:
            raw_text = ""

        try:
            from actions.assistant_output_action import AssistantOutputAction
            display_text = AssistantOutputAction.filter_full_text(raw_text, self.session)
            sanitized_text = AssistantOutputAction.filter_full_text_for_return(raw_text, self.session)
        except Exception:
            display_text = raw_text
            sanitized_text = raw_text
        return str(raw_text), str(display_text), str(sanitized_text)

    def _record_assistant(self, raw_text: str) -> None:
        try:
            chat = self.session.get_context("chat")
            if chat is None:
                chat = self.session.add_context("chat")
            chat.add(raw_text or "", "assistant")
        except Exception:
            pass

    def _execute_tools(self, text: str) -> bool:
        ran_any = False
        provider = None
        try:
            provider = self.session.get_provider()
        except Exception:
            provider = None

        # Skip tool execution if cancellation has been requested
        try:
            if self.session.get_flag('turn_cancelled'):
                return False
        except Exception:
            pass
        try:
            token = self.session.get_cancellation_token()
            if token and getattr(token, 'is_cancelled', None) and token.is_cancelled():
                return False
        except Exception:
            pass

        try:
            effective_mode = 'none'
            try:
                effective_mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
            except Exception:
                effective_mode = 'none'
            if effective_mode == 'official' and provider and hasattr(provider, 'get_tool_calls'):
                tool_calls = []
                try:
                    tool_calls = provider.get_tool_calls() or []
                except Exception:
                    tool_calls = []
                if tool_calls:
                    cmd_action = None
                    try:
                        cmd_action = self.session.get_action('assistant_commands')
                    except Exception:
                        cmd_action = None
                    commands_map = {}
                    if cmd_action and getattr(cmd_action, 'commands', None):
                        for key, spec in cmd_action.commands.items():
                            commands_map[str(key).lower()] = spec

                    try:
                        chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                        try:
                            chat_ctx.remove_last_message()
                        except Exception:
                            pass
                        chat_ctx.add('', role='assistant', extra={'tool_calls': tool_calls})
                    except Exception:
                        pass

                    cancelled_during_tools = False
                    for idx, call in enumerate(tool_calls):
                        # Respect cancellation between queued calls
                        try:
                            if self.session.get_flag('turn_cancelled'):
                                cancelled_during_tools = True
                                break
                        except Exception:
                            pass
                        try:
                            token = self.session.get_cancellation_token()
                            if token and getattr(token, 'is_cancelled', None) and token.is_cancelled():
                                cancelled_during_tools = True
                                break
                        except Exception:
                            pass
                        name = (call.get('name') or '').lower()
                        call_id = call.get('id') or call.get('call_id')
                        args = dict(call.get('arguments') or {})
                        content = ''
                        if 'content' in args:
                            content = args.pop('content') or ''
                        spec = commands_map.get(name)
                        if not spec:
                            # Fallback: map API-safe tool names back to canonical using stored mapping
                            try:
                                mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
                            except Exception:
                                mapping = {}
                            mapped = None
                            if isinstance(mapping, dict):
                                # Direct hit
                                mapped = mapping.get(name)
                                if not mapped:
                                    # Case-insensitive scan as a safety net
                                    try:
                                        for k, v in mapping.items():
                                            if isinstance(k, str) and k.lower() == name:
                                                mapped = v
                                                break
                                    except Exception:
                                        mapped = None
                            if mapped:
                                spec = commands_map.get(str(mapped).lower())
                            if not spec:
                                try:
                                    # Emit a tool_result stub so providers see a response for this call_id
                                    chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                    extra = {'tool_call_id': call_id} if call_id else None
                                    chat_ctx.add(f"Unsupported tool call: {name}", role='tool', extra=extra)
                                except Exception:
                                    pass
                                try:
                                    self.session.add_context('assistant', {
                                        'name': 'command_error',
                                        'content': f"Unsupported tool call: {name}"
                                    })
                                except Exception:
                                    pass
                                continue

                        handler = spec.get('function') or {}
                        # Merge fixed_args (for dynamic tools) before running; fixed override user-provided
                        try:
                            fixed = handler.get('fixed_args') if isinstance(handler, dict) else None
                            if isinstance(fixed, dict) and fixed:
                                merged = dict(args or {})
                                merged.update(fixed)
                                args = merged
                        except Exception:
                            pass
                        try:
                            from contextlib import nullcontext
                            self.session.utils.output.stop_spinner()
                            spinner_cm = nullcontext()
                            if not self.session.in_agent_mode():
                                spinner_cm = self.session.utils.output.spinner(f"Tool calling: {name}")
                        except Exception:
                            from contextlib import nullcontext
                            spinner_cm = nullcontext()

                        try:
                            before_ctx = list(self.session.get_contexts('assistant') or [])
                            before_len = len(before_ctx)
                        except Exception:
                            before_len = 0

                        try:
                            action = self.session.get_action(handler.get('name'))
                            with spinner_cm:
                                try:
                                    if not self.session.in_agent_mode():
                                        ui = getattr(self.session, 'ui', None)
                                        blocking = True
                                        try:
                                            blocking = bool(ui and ui.capabilities and ui.capabilities.blocking)
                                        except Exception:
                                            blocking = True
                                        if blocking:
                                            self.session.utils.output.write("")
                                            try:
                                                self.session.utils.output.stop_spinner()
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                                try:
                                    from utils.output_utils import OutputLevel
                                    out_mode = (self.session.get_params().get('agent_output_mode') or '').lower()
                                    if self.session.in_agent_mode() and out_mode in ('final', 'none'):
                                        with self.session.utils.output.suppress_below(OutputLevel.WARNING):
                                            action.run(args, content)
                                    else:
                                        action.run(args, content)
                                except KeyboardInterrupt:
                                    # Cooperative cancellation: mark token and stop executing queued calls
                                    try:
                                        self.session.set_flag('turn_cancelled', True)
                                    except Exception:
                                        pass
                                    try:
                                        tok = self.session.get_cancellation_token()
                                        if tok and hasattr(tok, 'cancel'):
                                            tok.cancel('keyboard')
                                    except Exception:
                                        pass
                                    # Emit tool_result for the current call to satisfy provider pairing
                                    try:
                                        chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                        extra = {'tool_call_id': call_id} if call_id else None
                                        chat_ctx.add('Cancelled', role='tool', extra=extra)
                                    except Exception:
                                        pass
                                    try:
                                        self.session.add_context('assistant', {
                                            'name': 'command_error',
                                            'content': f"Tool '{name}' cancelled by user"
                                        })
                                    except Exception:
                                        pass
                                    cancelled_during_tools = True
                                    break
                                except Exception:
                                    # Fallback: try once more without output suppression
                                    try:
                                        action.run(args, content)
                                    except KeyboardInterrupt:
                                        # Mirror cancellation handling in the main path
                                        try:
                                            self.session.set_flag('turn_cancelled', True)
                                        except Exception:
                                            pass
                                        try:
                                            tok = self.session.get_cancellation_token()
                                            if tok and hasattr(tok, 'cancel'):
                                                tok.cancel('keyboard')
                                        except Exception:
                                            pass
                                        try:
                                            chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                            extra = {'tool_call_id': call_id} if call_id else None
                                            chat_ctx.add('Cancelled', role='tool', extra=extra)
                                        except Exception:
                                            pass
                                        try:
                                            self.session.add_context('assistant', {
                                                'name': 'command_error',
                                                'content': f"Tool '{name}' cancelled by user"
                                            })
                                        except Exception:
                                            pass
                                        cancelled_during_tools = True
                                        break
                            ran_any = True
                            try:
                                after_ctx = list(self.session.get_contexts('assistant') or [])
                                # Collect new assistant outputs as a single text block for the tool result
                                new_items = after_ctx[before_len:] if (isinstance(after_ctx, list) and before_len <= len(after_ctx)) else []
                                tool_output_text = ''
                                if new_items:
                                    try:
                                        parts = []
                                        for item in new_items:
                                            # Accept both raw context objects and {type,context} wrappers
                                            ctx_obj = item.get('context') if isinstance(item, dict) else item
                                            data = ctx_obj.get() if hasattr(ctx_obj, 'get') else None
                                            if data and isinstance(data, dict):
                                                c = data.get('content')
                                                if isinstance(c, str) and c.strip():
                                                    parts.append(c)
                                        if parts:
                                            tool_output_text = "\n\n".join(parts)
                                    except Exception:
                                        tool_output_text = ''
                                # Append a tool role message with tool_call_id for OpenAI official tools
                                try:
                                    chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                    if tool_output_text.strip() == '':
                                        tool_output_text = 'OK'
                                    extra = {'tool_call_id': call_id} if call_id else None
                                    chat_ctx.add(tool_output_text, role='tool', extra=extra)
                                except Exception:
                                    pass
                                # Auto-submit follow-up turn
                                self.session.set_flag('auto_submit', True)
                            except Exception:
                                pass
                        except Exception as e:
                            # Make sure to emit a tool_result with the call_id even on error
                            try:
                                chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                extra = {'tool_call_id': call_id} if call_id else None
                                chat_ctx.add(f"Error: {e}", role='tool', extra=extra)
                            except Exception:
                                pass
                            try:
                                self.session.add_context('assistant', {
                                    'name': 'tool_error',
                                    'content': f"Tool '{name}' failed: {e}"
                                })
                            except Exception:
                                pass
                    # If cancelled during any tool call, emit tool_result stubs for remaining calls
                    try:
                        if cancelled_during_tools or self.session.get_flag('turn_cancelled'):
                            # Emit 'Cancelled' tool results for any unprocessed tool_calls
                            try:
                                remaining = tool_calls[idx + 1:] if 'idx' in locals() else []
                            except Exception:
                                remaining = []
                            for rem in remaining:
                                try:
                                    rem_id = rem.get('id') or rem.get('call_id')
                                    chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                    extra = {'tool_call_id': rem_id} if rem_id else None
                                    chat_ctx.add('Cancelled', role='tool', extra=extra)
                                except Exception:
                                    pass
                            return True
                    except Exception:
                        pass
                    try:
                        token = self.session.get_cancellation_token()
                        if token and getattr(token, 'is_cancelled', None) and token.is_cancelled():
                            # Also emit stubs if not already done
                            try:
                                remaining = tool_calls[idx + 1:] if 'idx' in locals() else []
                            except Exception:
                                remaining = []
                            for rem in remaining:
                                try:
                                    rem_id = rem.get('id') or rem.get('call_id')
                                    chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                    extra = {'tool_call_id': rem_id} if rem_id else None
                                    chat_ctx.add('Cancelled', role='tool', extra=extra)
                                except Exception:
                                    pass
                            return True
                    except Exception:
                        pass
                    # Add a separator newline after all tools finish (CLI only)
                    try:
                        if not self.session.in_agent_mode():
                            ui = getattr(self.session, 'ui', None)
                            blocking = True
                            try:
                                blocking = bool(ui and ui.capabilities and ui.capabilities.blocking)
                            except Exception:
                                blocking = True
                            if blocking:
                                self.session.utils.output.write("")
                    except Exception:
                        pass
                    return True
        except Exception:
            pass

        # Textual command handling via assistant_commands
        try:
            ac = self.session.get_action('assistant_commands')
            if ac and hasattr(ac, 'parse_commands') and callable(getattr(ac, 'parse_commands')):
                # If no text was provided, fall back to the last assistant message in chat
                if not text:
                    try:
                        chat = self.session.get_context('chat')
                        turns = chat.get('all') if chat else []
                        # Walk backwards to find the last assistant role
                        if isinstance(turns, list):
                            for t in reversed(turns):
                                if t.get('role') == 'assistant':
                                    text = t.get('content') or ''
                                    break
                    except Exception:
                        pass
                try:
                    cmds = ac.parse_commands(text or '')
                except Exception:
                    cmds = []
                if cmds:
                    # Allow InteractionNeeded to bubble up for Web/TUI handoff
                    ac.run(text or '')
                    ran_any = True
        except Exception as e:
            try:
                from base_classes import InteractionNeeded
                if isinstance(e, InteractionNeeded):
                    raise
            except Exception:
                pass
        return ran_any

    # ---- Utilities -----------------------------------------------------
    def _contains_sentinel(self, text: str, sentinels: List[str]) -> bool:
        try:
            if not text:
                return False
            return any(s in text for s in (sentinels or []))
        except Exception:
            return False

    def _strip_sentinels(self, text: Optional[str], sentinels: List[str]) -> Optional[str]:
        if not text:
            return text
        cleaned = text
        for s in (sentinels or []):
            cleaned = cleaned.replace(s, '')
        return cleaned

    def _dump_messages(self, *, header: str, include_prompt: bool, omit_last_assistant: bool) -> None:
        try:
            self.utils.output.write(f"\n==== {header} ====")
            provider = self.session.get_provider()
            if provider and hasattr(provider, 'get_messages'):
                msgs = provider.get_messages()
            else:
                msgs = []
            if not msgs:
                self.utils.output.write("(no provider-visible messages)")
                return
            import json
            self.utils.output.write(json.dumps(msgs, indent=2, ensure_ascii=False))
        except Exception:
            pass
