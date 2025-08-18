from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple


@dataclass
class TurnOptions:
    """Options that influence turn orchestration."""

    stream: Optional[bool] = None
    agent_output_mode: Optional[str] = None  # 'final' | 'full' | 'none'
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

    This runner centralizes the sequencing that currently exists in Chat/Agent/Web
    modes so all modes can share the same behavior for:
      - context processing and attachment to the next user turn
      - assistant generation (streaming and non-stream)
      - tool parsing/execution and auto-submit follow-ups
      - sentinel-based early stop (Agent flows)
    """

    def __init__(self, session) -> None:
        self.session = session
        self.utils = session.utils

    # ---- Public API ----------------------------------------------------
    def run_user_turn(self, input_text: str, *, options: Optional[TurnOptions] = None) -> TurnResult:
        """Run a single user-initiated turn with optional auto-submit follow-ups.

        Adds the user's message, processes contexts, runs the assistant once, runs
        tools (which may set auto_submit), and if allowed, runs follow-up assistant
        turns until auto_submit is cleared. Returns the final display/sanitized text.
        """
        opts = options or TurnOptions()
        params = self.session.get_params() or {}

        # Resolve defaults from session/config
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

        # Prepare contexts for the user turn; honor pre-set auto_submit from prior actions
        initial_auto = bool(self.session.get_flag("auto_submit"))
        contexts = self._process_contexts(auto_submit=initial_auto, suppress_print=opts.suppress_context_print)
        if initial_auto:
            # Reset to avoid loops; next message will be synthetic empty
            self.session.set_flag("auto_submit", False)
        self._add_user_message("" if initial_auto else (input_text or ""), contexts)
        self._clear_temp_contexts()

        # Run assistant + tools loop; continue while auto_submit is set and allowed
        safety_cap = 6  # prevent infinite tool loops
        for _ in range(safety_cap):
            raw, display, sanitized = self._assistant_turn(stream=stream, output_mode=None)
            turns_executed += 1
            last_display = display
            last_sanitized = sanitized

            # Record the assistant raw message in chat
            self._record_assistant(raw)

            # Sentinel early stop (mainly for Agent-type flows if used here)
            if display and self._contains_sentinel(display, opts.sentinels):
                stopped_on_sentinel = True
                break

            # Execute tools from sanitized text
            ran = self._execute_tools(sanitized or display or raw or "")
            any_tools = any_tools or ran

            # Check if we should auto-submit another assistant turn
            if allow_auto_submit and self.session.get_flag("auto_submit"):
                # Reset and prepare next synthetic user message with contexts
                self.session.set_flag("auto_submit", False)
                contexts2 = self._process_contexts(auto_submit=True, suppress_print=opts.suppress_context_print)
                self._add_user_message("", contexts2)
                self._clear_temp_contexts()
                # Continue loop to produce the follow-up assistant turn
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
        """Run up to N assistant turns (Agent Mode behavior) with tools between turns.

        - Injects a lightweight agent status context per turn (Turn X of Y).
        - On the first turn, a caller-provided hook can modify the prompt to add
          finish/write-policy instructions (mirrors current AgentMode behavior).
        - Honors early stop on sentinel tokens and optional no-tools heuristic.
        """
        opts = options or TurnOptions()
        params = self.session.get_params() or {}
        output_mode = (opts.agent_output_mode or params.get("agent_output_mode") or "final").lower()
        # Streaming is only used for 'full' output; 'final' and 'none' compute non-stream text
        stream = (output_mode == "full")

        total_turns = max(1, int(steps or 1))
        turns_executed = 0
        last_display: Optional[str] = None
        last_sanitized: Optional[str] = None
        stopped_on_sentinel = False
        any_tools = False

        # Ensure chat context exists
        if not self.session.get_context("chat"):
            self.session.add_context("chat")

        for i in range(total_turns):
            final_turn = (i == total_turns - 1)

            # Add per-turn agent status as a context
            status_text = f"Turn {i + 1} of {total_turns}"
            try:
                self.session.add_context("agent", {"name": "agent_status", "content": f"<status>{status_text}</status>"})
            except Exception:
                pass

            # Prepare contexts (print only when output is 'full') and synthetic user turn
            contexts = self._process_contexts(auto_submit=True, suppress_print=(output_mode != "full"))

            # Detect stdin content and remove it from contexts; use as the user message
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

            # Allow caller to prepare/inject prompt notes on first turn (hint about stdin)
            if i == 0 and prepare_prompt:
                try:
                    prepare_prompt(self.session, i + 1, total_turns, bool(stdin_idx is not None))
                except Exception:
                    pass

            # Add the user message (stdin content becomes the message when present)
            self._add_user_message(stdin_content or "", contexts)
            self._clear_temp_contexts()

            # Verbose dump before turn: after attaching user turn, before assistant
            if opts.verbose_dump:
                try:
                    self._dump_messages(
                        header=f"BEFORE TURN {i+1}",
                        include_prompt=(i == 0),
                        omit_last_assistant=True,
                    )
                except Exception:
                    pass

            # Produce one assistant response
            raw, display, sanitized = self._assistant_turn(stream=stream, output_mode=output_mode)
            turns_executed += 1
            last_display = display
            last_sanitized = sanitized

            # Record assistant raw message
            self._record_assistant(raw)

            # Early stop on sentinel
            text_for_stop = display or sanitized or raw or ""
            if text_for_stop and self._contains_sentinel(text_for_stop, opts.sentinels):
                stopped_on_sentinel = True
                break

            # Execute tools
            ran = self._execute_tools(sanitized or display or raw or "")
            any_tools = any_tools or ran

            # Optional heuristic: stop early if no tools and not final
            if (not final_turn) and opts.early_stop_no_tools and (not ran):
                break

        # For 'final' mode, trim sentinels from the last visible text
        if last_display and output_mode == "final":
            last_display = self._strip_sentinels(last_display, opts.sentinels)

        return TurnResult(
            last_text=last_display,
            sanitized=last_sanitized,
            turns_executed=turns_executed,
            stopped_on_sentinel=stopped_on_sentinel,
            ran_tools=any_tools,
        )

    # ---- Internals -----------------------------------------------------
    def _process_contexts(self, *, auto_submit: bool, suppress_print: bool) -> list:
        try:
            pc = self.session.get_action("process_contexts")
            if not pc:
                return []
            if suppress_print:
                # Silent collection of contexts (no printing)
                get_ctx = getattr(pc, "get_contexts", None)
                if callable(get_ctx):
                    return get_ctx(self.session) or []
                return []
            # Normal path: print summaries/details per current params
            if hasattr(pc, "process_contexts_for_user"):
                return pc.process_contexts_for_user(auto_submit=auto_submit) or []
        except Exception:
            pass
        return []

    # Pre-prompt updates for interactive modes (CLI/TUI):
    # Show context summaries and any assistant/agent details before collecting input.
    def show_pre_prompt_updates(self) -> None:
        try:
            pc = self.session.get_action("process_contexts")
            if pc and hasattr(pc, "process_contexts_for_user"):
                pc.process_contexts_for_user(auto_submit=False)
        except Exception:
            # Never fail the prompt loop due to pre-prompt updates
            pass

    def _add_user_message(self, text: str, contexts: list) -> None:
        chat = self.session.get_context("chat")
        if not chat:
            chat = self.session.add_context("chat")
        try:
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
        """Run one assistant turn and return (raw, display, sanitized)."""
        provider = self.session.get_provider()
        if not provider:
            return "", "", ""

        # Streaming path uses AssistantOutputAction to render tokens
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
                # Prefer sanitized/display from the action if available
                try:
                    display = (getattr(out_action, "get_display_output", None) or (lambda: None))() or raw
                except Exception:
                    display = raw
                try:
                    sanitized = (getattr(out_action, "get_sanitized_output", None) or (lambda: None))() or raw
                except Exception:
                    sanitized = raw
                return raw, str(display), str(sanitized)
            # Fallback: manual streaming loop
            try:
                for chunk in (stream_iter or []):
                    self.utils.output.write(chunk, end="", flush=True)
                    raw += chunk
                self.utils.output.write("")
            except Exception:
                pass
            return raw, raw, raw

        # Non-stream path: direct call and then display/sanitize filters
        try:
            raw_text = provider.chat()
        except Exception as e:
            raw_text = f"[error] {e}"
        if raw_text is None:
            raw_text = ""

        # Apply display-side filters for parity with CLI
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
        if not text:
            return False
        try:
            commands_action = self.session.get_action("assistant_commands")
        except Exception:
            commands_action = None
        if not commands_action:
            return False

        # Peek for commands to decide if any tool runs; then execute
        try:
            parsed = commands_action.parse_commands(text) or []
        except Exception:
            parsed = []
        try:
            if parsed:
                commands_action.run(text)
                return True
            return False
        except Exception as e:
            # Allow InteractionNeeded to propagate (Web/TUI will handle token issuance)
            try:
                from base_classes import InteractionNeeded  # late import to avoid cycles
            except Exception:
                InteractionNeeded = None  # type: ignore
            if InteractionNeeded and isinstance(e, InteractionNeeded):
                raise
            # Otherwise, bubble a minimal error context so the next turn can see it
            try:
                self.session.add_context(
                    "assistant",
                    {"name": "command_error", "content": f"Error running assistant commands: {e}"},
                )
            except Exception:
                pass
            return False

    @staticmethod
    def _contains_sentinel(text: str, sentinels: List[str]) -> bool:
        if not text:
            return False
        for tag in sentinels:
            if tag in text:
                return True
        return False

    @staticmethod
    def _strip_sentinels(text: Optional[str], sentinels: List[str]) -> Optional[str]:
        if not text:
            return text
        out = text
        for tag in sentinels:
            out = out.replace(tag, "")
        return out

    # Debug helper used by Agent verbose mode
    def _dump_messages(self, *, header: str, include_prompt: bool, omit_last_assistant: bool) -> None:
        out = self.utils.output
        try:
            out.write(f"=== {header} ===")
            if include_prompt:
                prompt_context = self.session.get_context('prompt')
                if prompt_context:
                    prompt_data = prompt_context.get()
                    content = prompt_data.get('content') if isinstance(prompt_data, dict) else None
                    if content:
                        out.write("--- SYSTEM PROMPT ---")
                        out.write(str(content))

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

            omit_idx = -1
            if omit_last_assistant:
                for idx in range(len(messages) - 1, -1, -1):
                    try:
                        if (messages[idx].get('role') == 'assistant'):
                            omit_idx = idx
                            break
                    except Exception:
                        continue

            for i, message in enumerate(messages):
                role = message.get('role', 'unknown')
                if role == 'system':
                    continue
                if i == omit_idx:
                    out.write(f"[{i}] {role.upper()}:")
                    continue
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
            pass
