from __future__ import annotations

from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterThinkTagAction(InteractionAction):
    """
    Hides content within <think>...</think> spans during streaming.
    - Emits a placeholder once per span
    - Handles spans that begin/end across chunk boundaries
    """

    # Mark that this filter affects the returned/sanitized output
    AFFECTS_RETURN = True

    def __init__(self, session):
        self.session = session
        # Default to blank unless configured
        self.think_placeholder = ""
        # Stateful across chunks
        self.in_think = False
        self._emitted_placeholder = False
        self._hidden_parts = []

    # Required by InteractionAction, not used for this filter
    def run(self, *args, **kwargs):  # pragma: no cover - not invoked
        return None

    def configure(self, opts: dict):
        self.think_placeholder = opts.get('think_placeholder', self.think_placeholder)

    def on_complete(self):  # noqa: D401 - optional lifecycle
        # Reset state after a stream completes
        self.in_think = False
        self._emitted_placeholder = False
        # Do not clear hidden parts here to allow retrieval post-run

    def process_token(self, text: str) -> Decision:
        if not text:
            return ("PASS", text)

        out = []
        i = 0
        open_tag = "<think>"
        close_tag = "</think>"
        lt = len(open_tag)
        lct = len(close_tag)

        while i < len(text):
            if not self.in_think:
                # Look for either an opener or a stray closer (which we'll strip)
                next_open = text.find(open_tag, i)
                next_close = text.find(close_tag, i)

                if next_open == -1 and next_close == -1:
                    out.append(text[i:])
                    break

                # If a stray closer appears before any opener, skip the tag
                if next_close != -1 and (next_open == -1 or next_close < next_open):
                    out.append(text[i:next_close])
                    i = next_close + lct
                    continue

                # Handle opener
                if next_open != -1 and (next_close == -1 or next_open <= next_close):
                    out.append(text[i:next_open])
                    self.in_think = True
                    self._emitted_placeholder = False
                    i = next_open + lt
                    continue

                # Fallback safety
                out.append(text[i:])
                break
            else:
                # We're inside a think block: emit placeholder once, drop content until close
                if not self._emitted_placeholder:
                    out.append(self.think_placeholder)
                    self._emitted_placeholder = True

                next_close = text.find(close_tag, i)
                if next_close == -1:
                    # Consume the rest of this chunk; remain in think
                    # Capture hidden segment
                    if i < len(text):
                        self._hidden_parts.append(text[i:])
                    i = len(text)
                else:
                    # Capture hidden segment before the close tag
                    if i < next_close:
                        self._hidden_parts.append(text[i:next_close])
                    # Finish the block and continue after the close tag
                    i = next_close + lct
                    self.in_think = False
                    self._emitted_placeholder = False

        return ("PASS", ''.join(out))

    def get_hidden(self) -> str:
        return ''.join(self._hidden_parts)
