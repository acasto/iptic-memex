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
        self._filters: List[Any] = []
        self._accumulated_raw: str = ""
        self._accumulated_filtered: str = ""
        self._think_placeholder = "⟦hidden:think⟧"
        self._tool_placeholder = "⟦hidden:{name}⟧"

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

    def _load_filters(self) -> None:
        names = self._parse_filters_from_params()
        self._filters = []
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
            # Optional configure()
            try:
                if hasattr(inst, 'configure') and callable(getattr(inst, 'configure')):
                    inst.configure(opts)
            except Exception as e:
                self.output.warning(f"Failed to configure filter '{name}': {e}")
            self._filters.append(inst)

        if self._filters:
            class_names = [f.__class__.__name__ for f in self._filters]
            self.output.debug(f"Loaded output filters: {class_names}")

    # ---- streaming callbacks ----
    def _apply_filters(self, text: str) -> str:
        if not self._filters:
            return text

        current = text
        for f in self._filters:
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
        self._accumulated_raw += token

        # Apply pipeline for user-visible text
        visible = self._apply_filters(token)
        if visible:
            self.output.write(visible, end='', flush=True)
            self._accumulated_filtered += visible
        return token

    def _on_complete(self, raw_full: str) -> str:
        # Finish filters lifecycle
        for f in self._filters:
            try:
                if hasattr(f, 'on_complete') and callable(getattr(f, 'on_complete')):
                    f.on_complete()
            except Exception:
                # Swallow to avoid breaking UX
                pass
        # Ensure final flush
        self.output.write('', flush=True)
        return raw_full

    def run(self, stream, spinner_message: str = "Processing response..."):
        """
        Process a stream, writing filtered text to console but returning raw text.
        """
        # Reset state and (re)load filters lazily per run
        self._accumulated_raw = ""
        self._accumulated_filtered = ""
        self._load_filters()

        raw_result = self.session.utils.stream.process_stream(
            stream,
            on_token=self._on_token,
            on_complete=self._on_complete,
            on_buffer=None,
            spinner_message=spinner_message
        )

        # Return raw result for internal consumers (e.g., assistant_commands)
        return raw_result
