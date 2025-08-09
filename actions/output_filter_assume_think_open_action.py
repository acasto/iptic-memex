from __future__ import annotations

from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterAssumeThinkOpenAction(InteractionAction):
    """
    Assumes we are inside a <think> span until a closing </think> is seen.
    Useful for models that omit the opening <think> tag.
    """

    def __init__(self, session):
        self.session = session
        self.think_placeholder = "⟦hidden:think⟧"
        # Start inside think by default
        self.in_think = True
        self._emitted_placeholder = False

    # Required by InteractionAction, not used for this filter
    def run(self, *args, **kwargs):  # pragma: no cover - not invoked
        return None

    def configure(self, opts: dict):
        self.think_placeholder = opts.get('think_placeholder', self.think_placeholder)

    def on_complete(self):  # noqa: D401 - optional lifecycle
        # Reset state after a stream completes
        self.in_think = True
        self._emitted_placeholder = False

    def process_token(self, text: str) -> Decision:
        if not text:
            return ("PASS", text)

        out = []
        i = 0
        close_tag = "</think>"
        lct = len(close_tag)

        while i < len(text):
            if not self.in_think:
                # Pass-through until the next closing (if any) appears; if it does, strip it
                next_close = text.find(close_tag, i)
                if next_close == -1:
                    out.append(text[i:])
                    break
                out.append(text[i:next_close])
                # Skip the close tag (extra stray)
                i = next_close + lct
                continue
            else:
                # Inside implicit think: emit placeholder once, drop content until a close tag
                if not self._emitted_placeholder:
                    out.append(self.think_placeholder)
                    self._emitted_placeholder = True

                next_close = text.find(close_tag, i)
                if next_close == -1:
                    i = len(text)
                else:
                    # Exit implicit think on close
                    i = next_close + lct
                    self.in_think = False
                    self._emitted_placeholder = False

        return ("PASS", ''.join(out))

