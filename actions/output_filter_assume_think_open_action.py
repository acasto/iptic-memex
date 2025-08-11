from __future__ import annotations

from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterAssumeThinkOpenAction(InteractionAction):
    """
    Assumes we are inside a <think> (or <thinking>) span until a closing </think> or </thinking> is seen.
    Useful for models that omit the opening <think> tag.
    """

    # Mark that this filter affects the returned/sanitized output
    AFFECTS_RETURN = True

    def __init__(self, session):
        self.session = session
        # Default to blank unless configured
        self.think_placeholder = ""
        # Start inside think by default
        self.in_think = True
        self._emitted_placeholder = False
        self._hidden_parts = []
        # Carry buffer to handle split close tokens across chunks
        self._carry = ""

    # Required by InteractionAction, not used for this filter
    def run(self, *args, **kwargs):  # pragma: no cover - not invoked
        return None

    def configure(self, opts: dict):
        self.think_placeholder = opts.get('think_placeholder', self.think_placeholder)

    def on_complete(self):  # noqa: D401 - optional lifecycle
        """Finalize and optionally flush any visible tail.

        Returns a string that should be appended to visible output if any.
        """
        tail = ""
        # If we ended outside implicit think, any carry is visible
        if self._carry and not self.in_think:
            tail = self._carry
        # If still inside implicit think, carry is hidden
        elif self._carry and self.in_think:
            self._hidden_parts.append(self._carry)

        # Reset state after a stream completes
        self.in_think = True
        self._emitted_placeholder = False
        self._carry = ""
        # Do not clear hidden parts here to allow retrieval post-run
        return tail

    def process_token(self, text: str) -> Decision:
        if not text:
            return ("PASS", text)

        buf = self._carry + text
        out = []
        i = 0
        close_tags = ("</think>", "</thinking>")
        close_lens = {t: len(t) for t in close_tags}
        max_close_len = max(close_lens.values())
        n = len(buf)

        while i < n:
            if not self.in_think:
                # Pass-through until the next closing (if any) appears; if it does, strip it
                next_close = -1
                close_tag_hit = None
                for t in close_tags:
                    pos = buf.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_tag_hit = t
                if next_close == -1:
                    # Emit visible content; keep only a tail that could start a closer prefix
                    keep_from = n
                    last_lt = buf.rfind('<', i)
                    if last_lt != -1:
                        candidate = buf[last_lt:n]
                        if any(t.startswith(candidate) for t in close_tags):
                            keep_from = last_lt
                    if i < keep_from:
                        out.append(buf[i:keep_from])
                    # Keep carry only if we have a possible closer prefix
                    self._carry = buf[keep_from:] if keep_from < n else ""
                    return ("PASS", ''.join(out))
                out.append(buf[i:next_close])
                # Skip the close tag (extra stray)
                i = next_close + close_lens[close_tag_hit]
                continue
            else:
                # Inside implicit think: emit placeholder once, drop content until a close tag
                if not self._emitted_placeholder:
                    out.append(self.think_placeholder)
                    self._emitted_placeholder = True

                next_close = -1
                close_tag_hit = None
                for t in close_tags:
                    pos = buf.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_tag_hit = t
                if next_close == -1:
                    # Capture the hidden remainder
                    if i < n:
                        self._hidden_parts.append(buf[i:])
                    # Keep a tail to detect split closer across boundary
                    tail_len = max_close_len - 1
                    keep_from = max(n - tail_len, i)
                    self._carry = buf[keep_from:]
                    return ("PASS", ''.join(out))
                else:
                    # Capture hidden segment before the close tag
                    if i < next_close:
                        self._hidden_parts.append(buf[i:next_close])
                    # Exit implicit think on close
                    i = next_close + close_lens[close_tag_hit]
                    self.in_think = False
                    self._emitted_placeholder = False

        # Fully consumed buffer -> clear carry
        self._carry = ""
        return ("PASS", ''.join(out))

    def get_hidden(self) -> str:
        return ''.join(self._hidden_parts)

    def clear_hidden(self) -> None:
        self._hidden_parts.clear()
