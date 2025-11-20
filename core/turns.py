from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple, Literal
import time
import random
from base_classes import InteractionNeeded
from contexts.chat_context import ChatContext as RealChatContext


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
        user_meta = self._begin_turn_meta(role="user", kind="auto_submit" if initial_auto else "user")
        contexts = self._process_contexts(auto_submit=initial_auto, suppress_print=opts.suppress_context_print)
        if initial_auto:
            self.session.set_flag("auto_submit", False)
        self._add_user_message("" if initial_auto else (input_text or ""), contexts, user_meta)
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
                meta2 = self._begin_turn_meta(role="user", kind="auto_submit")
                contexts2 = self._process_contexts(auto_submit=True, suppress_print=opts.suppress_context_print)
                if self.session.get_flag("auto_submit"):
                    self.session.set_flag("auto_submit", False)
                    self._add_user_message("", contexts2, meta2)
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

            user_meta = self._begin_turn_meta(role="user", kind="agent_step")
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

            self._add_user_message(stdin_content or "", contexts, user_meta)
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

    def _chat_add(self, chat, text: str, role: str, contexts: Optional[list] = None, extra: Optional[dict] = None) -> None:
        """
        Helper to add a message to chat while remaining compatible with both the
        real ChatContext (which supports `extra`) and lightweight test doubles.
        """
        try:
            if isinstance(chat, RealChatContext):
                if contexts is None:
                    chat.add(text or "", role, extra=extra)
                else:
                    chat.add(text or "", role, contexts or [], extra=extra)
            else:
                # Test doubles typically accept (message, role, contexts=None)
                if contexts is None:
                    chat.add(text or "", role)
                else:
                    chat.add(text or "", role, contexts or [])
        except TypeError:
            # Fallback for unexpected signatures: ignore contexts/extra
            try:
                chat.add(text or "", role)
            except Exception:
                pass

    def _clear_turn_status_contexts(self) -> None:
        """Remove any pending turn_status contexts to avoid leaking across turns."""
        try:
            contexts = self.session.context.get("assistant", [])
            if not isinstance(contexts, list) or not contexts:
                return
            keep = []
            for ctx in contexts:
                try:
                    data = ctx.get() if hasattr(ctx, "get") else None
                    if isinstance(data, dict) and data.get("name") == "turn_status":
                        continue
                except Exception:
                    pass
                keep.append(ctx)
            if len(keep) != len(contexts):
                self.session.context["assistant"] = keep
                if not keep:
                    self.session.context.pop("assistant", None)
        except Exception:
            pass

    @staticmethod
    def _short_id(index_hint: Optional[int] = None, suffix_len: int = 4) -> str:
        """Generate a compact, anchored-looking id like 't9-3xf7'."""
        def to_b36(n: int) -> str:
            if n <= 0:
                return "0"
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"
            out = ""
            while n:
                n, r = divmod(n, 36)
                out = digits[r] + out
            return out

        idx_part = f"t{to_b36(index_hint)}" if index_hint is not None else "t0"
        tail = "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(suffix_len))
        return f"{idx_part}-{tail}"

    def _begin_turn_meta(self, *, role: str, kind: str, add_status_context: bool = True, build_prompt: bool = True) -> Optional[dict]:
        """Initialize per-turn metadata and optional turn_prompt context."""
        meta: dict = {"role": role, "kind": kind}
        try:
            chat = self.session.get_context("chat")
            if chat is None:
                chat = self.session.add_context("chat")
            try:
                history = chat.get("all")
            except Exception:
                history = []
            try:
                next_index = len(history) + 1
            except Exception:
                next_index = 1
            meta["index"] = next_index
        except Exception:
            # Best-effort; index is optional
            pass

        # Assign a stable identifier for this turn
        try:
            idx_hint = meta.get("index")
            meta.setdefault("id", self._short_id(idx_hint if isinstance(idx_hint, int) else None))
        except Exception:
            try:
                if "index" in meta:
                    meta.setdefault("id", str(meta["index"]))
            except Exception:
                meta.setdefault("id", "unknown")

        if kind == "auto_submit":
            meta["auto_submit"] = True
        try:
            meta["agent"] = bool(self.session.in_agent_mode())
        except Exception:
            pass

        # Make metadata visible to template handlers
        try:
            self.session.set_user_data("__turn_meta__", meta)
        except Exception:
            pass

        # Build and attach optional turn prompt text
        if build_prompt:
            turn_prompt = ""
            try:
                builder = self.session.get_action("build_turn_prompt")
            except Exception:
                builder = None
            if builder:
                try:
                    turn_prompt = builder.run(meta) or ""
                except Exception:
                    turn_prompt = ""
            if turn_prompt:
                meta["turn_prompt_text"] = turn_prompt
                if add_status_context:
                    # Ensure prior turn_status contexts are cleared before adding a new one
                    self._clear_turn_status_contexts()
                    # Inject as a transient assistant context so it is folded into the
                    # next turn's context without persisting in chat history.
                    try:
                        self.session.add_context(
                            "assistant",
                            {"name": "turn_status", "content": turn_prompt, "meta": {"kind": "turn_status"}},
                        )
                    except Exception:
                        pass

        return meta

    def _add_user_message(self, text: str, contexts: list, meta: Optional[dict] = None) -> None:
        try:
            chat = self.session.get_context("chat")
            if chat is None:
                chat = self.session.add_context("chat")
            extra = {"meta": meta} if isinstance(meta, dict) else None
            self._chat_add(chat, text or "", "user", contexts or [], extra=extra)
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
        # Log provider start
        try:
            meta = {
                'model': (self.session.get_params() or {}).get('model'),
                'provider': provider.__class__.__name__,
                'stream': bool(stream),
                'output_mode': output_mode or 'raw',
            }
            self.session.utils.logger.provider_start(meta, component='core.turns')
        except Exception:
            pass

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
                try:
                    self.session.utils.logger.provider_done({'result': 'ok', 'bytes': len(raw or '')}, component='core.turns')
                except Exception:
                    pass
                return raw, str(display), str(sanitized)
            try:
                for chunk in (stream_iter or []):
                    self.utils.output.write(chunk, end="", flush=True)
                    raw += chunk
                self.utils.output.write("")
            except Exception:
                pass
            try:
                self.session.utils.logger.provider_done({'result': 'ok', 'bytes': len(raw or '')}, component='core.turns')
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
            _st = time.time()
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

        # Log provider done (non-stream)
        try:
            duration_ms = int((time.time() - _st) * 1000) if '_st' in locals() and _st else None
        except Exception:
            duration_ms = None
        try:
            payload = {'result': 'ok', 'bytes': len(raw_text or '')}
            if duration_ms is not None:
                payload['duration_ms'] = duration_ms
            self.session.utils.logger.provider_done(payload, component='core.turns')
        except Exception:
            pass

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
            extra: Optional[dict] = None
            provider = None
            try:
                provider = self.session.get_provider()
            except Exception:
                provider = None
            if provider and hasattr(provider, 'get_current_reasoning'):
                try:
                    reasoning = provider.get_current_reasoning()
                    if reasoning:
                        extra = {'reasoning_content': reasoning}
                except Exception:
                    extra = None

            # Initialize per-turn metadata for this assistant message
            meta = self._begin_turn_meta(role="assistant", kind="assistant", add_status_context=False, build_prompt=False)
            if extra is None:
                extra = {}
            if isinstance(extra, dict) and isinstance(meta, dict):
                # Do not clobber existing keys (e.g., reasoning_content)
                if 'meta' in extra and isinstance(extra['meta'], dict):
                    merged_meta = dict(extra['meta'])
                    for k, v in meta.items():
                        if k not in merged_meta:
                            merged_meta[k] = v
                    extra['meta'] = merged_meta
                else:
                    extra.setdefault('meta', meta)
            self._chat_add(chat, raw_text or "", "assistant", None, extra)
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
                            last_turn = None
                            try:
                                history = chat_ctx.get()
                                if history:
                                    last_turn = history[-1]
                            except Exception:
                                last_turn = None
                            try:
                                chat_ctx.remove_last_message()
                            except Exception:
                                pass
                            extra_fields = {'tool_calls': tool_calls}
                            if last_turn:
                                try:
                                    if 'reasoning_content' in last_turn:
                                        extra_fields['reasoning_content'] = last_turn['reasoning_content']
                                except Exception:
                                    pass
                                # Preserve prior per-turn metadata when present
                                try:
                                    last_meta = last_turn.get('meta')
                                except Exception:
                                    last_meta = None
                                if isinstance(last_meta, dict):
                                    extra_fields['meta'] = dict(last_meta)
                            self._chat_add(chat_ctx, '', 'assistant', None, extra_fields)
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
                        # Log tool begin (official)
                        try:
                            self.session.utils.logger.tool_begin(name=name, call_id=call_id, args_summary=args, source='official')
                        except Exception:
                            pass
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
                        desc: Optional[str] = None
                        try:
                            from contextlib import nullcontext
                            self.session.utils.output.stop_spinner()
                            spinner_cm = nullcontext()
                            if not self.session.in_agent_mode():
                                # Prefer a user-provided short description (args.desc) when available
                                try:
                                    desc = args.get('desc') if isinstance(args, dict) else None
                                    if isinstance(desc, str):
                                        desc = desc.strip()
                                    # Fallback summary for common tools
                                    if not desc:
                                        if name == 'cmd':
                                            cmd = (args.get('command') or '') if isinstance(args, dict) else ''
                                            arg_s = (args.get('arguments') or '') if isinstance(args, dict) else ''
                                            content_s = content or ''
                                            joined = (f"{cmd} {arg_s}" if cmd else content_s).strip()
                                            if joined:
                                                desc = joined
                                    if isinstance(desc, str) and len(desc) > 120:
                                        desc = desc[:117] + '...'
                                    msg = f"Tool calling: {name}" + (f" — {desc}" if desc else "")
                                except Exception:
                                    msg = f"Tool calling: {name}"
                                spinner_cm = self.session.utils.output.spinner(msg)
                        except Exception:
                            from contextlib import nullcontext
                            spinner_cm = nullcontext()

                        try:
                            tool_scope_callable = getattr(self.session.utils.output, 'tool_scope', None)
                            if callable(tool_scope_callable):
                                scope_cm = tool_scope_callable(name, call_id=call_id, title=desc)
                            else:
                                from contextlib import nullcontext
                                scope_cm = nullcontext()
                        except Exception:
                            from contextlib import nullcontext
                            scope_cm = nullcontext()

                        try:
                            before_ctx = list(self.session.get_contexts('assistant') or [])
                            before_len = len(before_ctx)
                        except Exception:
                            before_len = 0

                        try:
                            action = self.session.get_action(handler.get('name'))
                            with spinner_cm:
                                with scope_cm:
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
                                        try:
                                            chat_ctx = self.session.get_context('chat') or self.session.add_context('chat')
                                            extra = {'tool_call_id': call_id} if call_id else None
                                            self._chat_add(chat_ctx, 'Cancelled', 'tool', None, extra)
                                        except Exception:
                                            pass
                                        try:
                                            self.session.add_context('assistant', {
                                                'name': 'command_error',
                                                'content': f"Tool '{name}' cancelled by user"
                                            })
                                        except Exception:
                                            pass
                                        try:
                                            self.session.utils.logger.tool_end(name=name, call_id=call_id, status='cancelled')
                                        except Exception:
                                            pass
                                        cancelled_during_tools = True
                                        break
                                    except InteractionNeeded as need:
                                        # Central mid-turn interaction handling for tools
                                        broker = None
                                        try:
                                            broker = self.session.get_user_data('__interaction_broker__')
                                        except Exception:
                                            broker = None
                                        prompt_fn = getattr(broker, 'prompt', None)
                                        if not callable(prompt_fn):
                                            # Fallback for older adapters
                                            try:
                                                prompt_fn = self.session.get_user_data('__interaction_prompt__')
                                            except Exception:
                                                prompt_fn = None
                                        if callable(prompt_fn):
                                            try:
                                                self.session.ui.emit('status', {'message': f"Tool requires input: {need.kind}", 'scope': 'tool'})
                                            except Exception:
                                                pass
                                            response = prompt_fn(need)
                                            if response is None:
                                                cancelled_during_tools = True
                                                try:
                                                    self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                                except Exception:
                                                    pass
                                                break
                                            # Drive resume loop in case of multi-step confirmations
                                            token = getattr(need, 'state_token', '__unknown__')
                                            while True:
                                                try:
                                                    action.resume(token, response)
                                                    break
                                                except InteractionNeeded as need2:
                                                    response = prompt_fn(need2)
                                                    if response is None:
                                                        cancelled_during_tools = True
                                                        try:
                                                            self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                                        except Exception:
                                                            pass
                                                        break
                                                    token = getattr(need2, 'state_token', '__unknown__')
                                            if cancelled_during_tools:
                                                break
                                            # Resumed successfully; proceed to emit tool result for this call_id
                                        # No handler available: re-raise for outer adapters
                                        raise
                                    except Exception:
                                        # Fallback: try once more without output suppression
                                        try:
                                            action.run(args, content)
                                        except KeyboardInterrupt:
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
                                                self._chat_add(chat_ctx, 'Cancelled', 'tool', None, extra)
                                            except Exception:
                                                pass
                                            try:
                                                self.session.add_context('assistant', {
                                                    'name': 'command_error',
                                                    'content': f"Tool '{name}' cancelled by user"
                                                })
                                            except Exception:
                                                pass
                                            try:
                                                self.session.utils.logger.tool_end(name=name, call_id=call_id, status='cancelled')
                                            except Exception:
                                                pass
                                            cancelled_during_tools = True
                                            break
                                        except InteractionNeeded as need:
                                            broker = None
                                            try:
                                                broker = self.session.get_user_data('__interaction_broker__')
                                            except Exception:
                                                broker = None
                                            prompt_fn = getattr(broker, 'prompt', None)
                                            if not callable(prompt_fn):
                                                try:
                                                    prompt_fn = self.session.get_user_data('__interaction_prompt__')
                                                except Exception:
                                                    prompt_fn = None
                                            if callable(prompt_fn):
                                                try:
                                                    self.session.ui.emit('status', {'message': f"Tool requires input: {need.kind}", 'scope': 'tool'})
                                                except Exception:
                                                    pass
                                                response = prompt_fn(need)
                                                if response is None:
                                                    cancelled_during_tools = True
                                                    try:
                                                        self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                                    except Exception:
                                                        pass
                                                    break
                                                token = getattr(need, 'state_token', '__unknown__')
                                                while True:
                                                    try:
                                                        action.resume(token, response)
                                                        break
                                                    except InteractionNeeded as need2:
                                                        response = prompt_fn(need2)
                                                        if response is None:
                                                            cancelled_during_tools = True
                                                            try:
                                                                self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                                            except Exception:
                                                                pass
                                                            break
                                                        token = getattr(need2, 'state_token', '__unknown__')
                                                if cancelled_during_tools:
                                                    break
                                                # Resumed successfully; proceed to emit tool result for this call_id
                                            raise
                                        except Exception as err:
                                            raise err
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
                            # Log successful tool execution summary
                            try:
                                after_ctx2 = list(self.session.get_contexts('assistant') or [])
                                delta = max(0, len(after_ctx2) - (before_len if 'before_len' in locals() else 0))
                            except Exception:
                                delta = 0
                            try:
                                self.session.set_user_data('__last_tool_scope__', {
                                    'tool_name': name,
                                    'tool_call_id': call_id,
                                    'title': desc,
                                })
                            except Exception:
                                pass
                            try:
                                self.session.utils.logger.tool_end(name=name, call_id=call_id, status='success', result_meta={'assistant_additions': delta})
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
                            try:
                                self.session.utils.logger.tool_end(name=name, call_id=call_id, status='error', result_meta={'error': str(e)})
                            except Exception:
                                pass
                            try:
                                self.session.utils.logger.tool_end(name=name, call_id=call_id, status='error', result_meta={'error': str(e)})
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
                    # Run pseudo-tools and handle mid-turn InteractionNeeded via the interaction broker
                    try:
                        ac.run(text or '')
                        ran_any = True
                    except InteractionNeeded as need:
                        # Expect assistant_commands to annotate need.spec with __action__/__args__/__content__
                        spec = getattr(need, 'spec', {}) or {}
                        action_name = spec.get('__action__')
                        action_args = spec.get('__args__')
                        action_content = spec.get('__content__')
                        action = None
                        try:
                            action = self.session.get_action(action_name) if action_name else None
                        except Exception:
                            action = None
                        # Find broker
                        broker = None
                        try:
                            broker = self.session.get_user_data('__interaction_broker__')
                        except Exception:
                            broker = None
                        prompt_fn = getattr(broker, 'prompt', None)
                        if not callable(prompt_fn):
                            # No TUI/Web prompt available; re-raise to outer handler
                            raise
                        # Establish a tool scope so prints (diffs, statuses) render in a tool bubble
                        try:
                            scope_callable = getattr(self.session.utils.output, 'tool_scope', None)
                        except Exception:
                            scope_callable = None
                        if callable(scope_callable):
                            scope_cm = scope_callable((action_name or 'tool'), call_id=None, title=None)
                        else:
                            from contextlib import nullcontext
                            scope_cm = nullcontext()
                        try:
                            # Remember scope so auto-submit context grouping uses the tool bubble
                            self.session.set_user_data('__last_tool_scope__', {
                                'tool_name': (action_name or 'tool'),
                                'tool_call_id': None,
                                'title': None,
                            })
                        except Exception:
                            pass
                        with scope_cm:
                            try:
                                self.session.ui.emit('status', {'message': f"Tool requires input: {need.kind}", 'scope': 'tool'})
                            except Exception:
                                pass
                            response = prompt_fn(need)
                            if response is None or not action:
                                try:
                                    self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                except Exception:
                                    pass
                                return True
                            token = getattr(need, 'state_token', '__unknown__')
                            # Rehydrate action state so resume() has args/content
                            try:
                                if isinstance(action_args, dict):
                                    setattr(action, '_current_args', action_args)
                                if action_content is not None:
                                    setattr(action, '_current_content', action_content)
                            except Exception:
                                pass
                            # Drive resume loop for multi-step interactions
                            while True:
                                try:
                                    action.resume(token, {'response': response, 'state': {'args': action_args, 'content': action_content}})
                                    break
                                except InteractionNeeded as need2:
                                    try:
                                        self.session.ui.emit('status', {'message': f"Tool requires input: {need2.kind}", 'scope': 'tool'})
                                    except Exception:
                                        pass
                                    response = prompt_fn(need2)
                                    if response is None:
                                        try:
                                            self.session.ui.emit('warning', {'message': 'Tool confirmation cancelled.', 'scope': 'tool'})
                                        except Exception:
                                            pass
                                        return True
                                    token = getattr(need2, 'state_token', '__unknown__')
                        ran_any = True
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
