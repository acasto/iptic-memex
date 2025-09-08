from base_classes import InteractionAction
from typing import List, Optional, Tuple, Any


Decision = Optional[Tuple[str, Optional[str]]]


class AssistantOutputAction(InteractionAction):
    """
    Streams assistant output while applying an optional, config-driven filter pipeline.
    - Writes post-filtered text to the user (preserves streaming UX)
    - Returns the raw, unfiltered text for internal consumers (e.g., command parsing)
    """

    def __init__(self, session):
        self.session = session
        self.output = session.utils.output
        # Separate pipelines: display (what user sees) and return (what ChatMode passes to parser)
        self._filters_display: List[Any] = []
        self._filters_return: List[Any] = []
        # Accumulators use list+join for performance on many small chunks
        self._accumulated_raw_parts: List[str] = []
        self._accumulated_display_parts: List[str] = []
        self._accumulated_return_parts: List[str] = []
        # Defaults: blank placeholders unless configured
        self._think_placeholder = ""
        self._tool_placeholder = ""

    # ---- filter lifecycle ----
    def _parse_filters_from_params(self) -> List[str]:
        params = self.session.get_params() or {}
        spec = params.get('output_filters')
        if not spec:
            return []
        if isinstance(spec, list):
            return [str(s).strip() for s in spec if str(s).strip()]
        if isinstance(spec, str):
            # CSV string
            return [s.strip() for s in spec.split(',') if s.strip()]
        return []

    def _load_placeholders(self) -> None:
        params = self.session.get_params() or {}
        self._think_placeholder = params.get('think_placeholder', self._think_placeholder)
        self._tool_placeholder = params.get('tool_placeholder', self._tool_placeholder)

    def _debug_log(self, message: str) -> None:
        try:
            params = self.session.get_params() or {}
            if params.get('debug_filters', False):
                self.output.debug(f"[OutputFilter] {message}")
        except Exception:
            pass

    def _load_filters(self) -> None:
        names = self._parse_filters_from_params()
        self._filters_display = []
        self._filters_return = []
        self._load_placeholders()

        opts = {
            'think_placeholder': self._think_placeholder,
            'tool_placeholder': self._tool_placeholder,
        }

        for name in names:
            # Use naming convention actions/output_filter_<name>_action.py
            action_name = f"output_filter_{name}"
            inst = self.session.get_action(action_name)
            if not inst:
                self.output.debug(f"Output filter not found: {name}")
                continue
            # Basic interface validation
            if not hasattr(inst, 'process_token') or not callable(getattr(inst, 'process_token')):
                self._debug_log(f"Filter '{name}' missing process_token(); skipping")
                continue
            # Optional configure()
            try:
                if hasattr(inst, 'configure') and callable(getattr(inst, 'configure')):
                    inst.configure(opts)
            except Exception as e:
                self.output.warning(f"Failed to configure filter '{name}': {e}")
            self._filters_display.append(inst)

            # If the filter should affect the returned output (e.g., strip <think>), create a separate instance
            affects_return = getattr(inst, 'AFFECTS_RETURN', False)
            if affects_return:
                inst_ret = self.session.get_action(action_name)
                if not hasattr(inst_ret, 'process_token') or not callable(getattr(inst_ret, 'process_token')):
                    self._debug_log(f"Return filter '{name}' missing process_token(); skipping")
                    inst_ret = None
                try:
                    if inst_ret and hasattr(inst_ret, 'configure') and callable(getattr(inst_ret, 'configure')):
                        inst_ret.configure(opts)
                except Exception as e:
                    self.output.warning(f"Failed to configure return filter '{name}': {e}")
                if inst_ret:
                    self._filters_return.append(inst_ret)

        if self._filters_display:
            class_names = [f.__class__.__name__ for f in self._filters_display]
            self.output.debug(f"Loaded output filters: {class_names}")

    # ---- streaming callbacks ----
    def _apply_filters(self, text: str, filters: List[Any]) -> str:
        if not filters:
            return text

        current = text
        for f in filters:
            try:
                decision: Decision = f.process_token(current)
            except Exception as e:
                # Never break streaming on filter error
                self.output.warning(f"Filter {f.__class__.__name__} error: {e}")
                decision = None

            if decision is None:
                # Pass-through unchanged
                continue

            action, payload = decision
            if action == 'PASS':
                current = current if payload is None else payload
                continue
            if action == 'DROP':
                return ''
            if action == 'REPLACE':
                return payload or ''

        return current

    def _on_token(self, token: str) -> str:
        # Accumulate raw
        self._accumulated_raw_parts.append(token)

        # Apply pipeline for user-visible text
        visible = self._apply_filters(token, self._filters_display)
        if visible:
            self.output.write(visible, end='', flush=True)
            self._accumulated_display_parts.append(visible)

        # Apply return-only pipeline (e.g., think filters) for internal parser input
        returned = self._apply_filters(token, self._filters_return)
        if returned:
            self._accumulated_return_parts.append(returned)
        return token

    def _on_complete(self, raw_full: str) -> str:
        # Finish filters lifecycle and flush any pending visible tails
        # Display filters: may return a string to append to visible output
        for f in self._filters_display:
            try:
                if hasattr(f, 'on_complete') and callable(getattr(f, 'on_complete')):
                    tail = f.on_complete()
                    if isinstance(tail, str) and tail:
                        self.output.write(tail, end='', flush=True)
                        self._accumulated_display_parts.append(tail)
            except Exception:
                # Swallow to avoid breaking UX
                pass

        # Return-only filters: may return a string to append to sanitized output
        for f in self._filters_return:
            try:
                if hasattr(f, 'on_complete') and callable(getattr(f, 'on_complete')):
                    tail = f.on_complete()
                    if isinstance(tail, str) and tail:
                        self._accumulated_return_parts.append(tail)
            except Exception:
                pass

        # Ensure final flush
        self.output.write('', flush=True)
        return raw_full

    def run(self, stream, spinner_message: str = "Processing response..."):
        """
        Process a stream, writing filtered text to console but returning raw text.
        """
        # Reset state and (re)load filters lazily per run
        self._accumulated_raw_parts = []
        self._accumulated_display_parts = []
        self._accumulated_return_parts = []
        self._load_filters()

        # Allow cooperative cancellation when output sink provides a cancel_check
        cancel_check = getattr(self.output, 'cancel_check', None)
        raw_result = self.session.utils.stream.process_stream(
            stream,
            on_token=self._on_token,
            on_complete=self._on_complete,
            on_buffer=None,
            spinner_message=spinner_message,
            spinner_style=None,
            cancel_check=cancel_check
        )

        # Return raw result for compatibility; ChatMode can query sanitized via getter
        return raw_result

    # ---- getters for other consumers ----
    def get_raw_output(self) -> str:
        return ''.join(self._accumulated_raw_parts)

    def get_display_output(self) -> str:
        return ''.join(self._accumulated_display_parts)

    def get_sanitized_output(self) -> str:
        return ''.join(self._accumulated_return_parts)

    def get_hidden_output(self) -> str:
        """Aggregate any hidden content captured by filters that expose get_hidden()."""
        parts: List[str] = []
        for f in self._filters_display + self._filters_return:
            try:
                if hasattr(f, 'get_hidden') and callable(getattr(f, 'get_hidden')):
                    hidden = f.get_hidden()
                    if hidden:
                        parts.append(hidden)
                # Optional cleanup to avoid accumulation if action reused
                if hasattr(f, 'clear_hidden') and callable(getattr(f, 'clear_hidden')):
                    f.clear_hidden()
            except Exception:
                continue
        return ''.join(parts)

    # ---- non-streaming helper ----
    @classmethod
    def filter_full_text(cls, text: str, session) -> str:
        """
        Apply the configured output filters to a complete message string.
        Mirrors the display pipeline used during streaming, without side effects.
        """
        try:
            inst = cls(session)
            # Prepare filters using the same config path as streaming
            inst._load_filters()
            # Apply display filters to the whole text in one pass
            filtered = inst._apply_filters(text, inst._filters_display)
            # Allow filters to finalize any state and flush any visible tail
            for f in inst._filters_display:
                try:
                    if hasattr(f, 'on_complete') and callable(getattr(f, 'on_complete')):
                        tail = f.on_complete()
                        if isinstance(tail, str) and tail:
                            filtered += tail
                except Exception:
                    # Non-fatal in non-streaming path
                    pass
            return filtered
        except Exception as e:
            # Log in debug paths, but never break UX
            try:
                session.utils.output.warning(f"filter_full_text error: {e}")
            except Exception:
                pass
            return text

    @classmethod
    def filter_full_text_for_return(cls, text: str, session) -> str:
        """
        Apply only the return-affecting filters (AFFECTS_RETURN=True) to a full string.
        This mirrors the sanitized output used for tool/command parsing during streaming.
        """
        try:
            inst = cls(session)
            inst._load_filters()
            sanitized = inst._apply_filters(text, inst._filters_return)
            # Finalize and append any tail returned by return-affecting filters
            for f in inst._filters_return:
                try:
                    if hasattr(f, 'on_complete') and callable(getattr(f, 'on_complete')):
                        tail = f.on_complete()
                        if isinstance(tail, str) and tail:
                            sanitized += tail
                except Exception:
                    pass
            return sanitized
        except Exception as e:
            try:
                session.utils.output.warning(f"filter_full_text_for_return error: {e}")
            except Exception:
                pass
            return text
