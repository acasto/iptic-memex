from __future__ import annotations

from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterThinkTagAction(InteractionAction):
    """
    Hides content within <think>...</think> (and <thinking>...</thinking>) spans during streaming.
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
        # Carry buffer to handle split tags across chunk boundaries
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
        # If we end outside of a think span, any remaining carry is visible text
        if self._carry and not self.in_think:
            tail = self._carry
        # If we end inside a think span, carry belongs to hidden content
        elif self._carry and self.in_think:
            self._hidden_parts.append(self._carry)

        # Reset state after stream completes
        self.in_think = False
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
        open_tags = ("<think>", "<thinking>")
        close_tags = ("</think>", "</thinking>")
        open_lens = {t: len(t) for t in open_tags}
        close_lens = {t: len(t) for t in close_tags}
        # Keep a small tail to detect boundary-spanning tags
        max_tag_len = max(max(open_lens.values()), max(close_lens.values()))

        n = len(buf)
        while i < n:
            if not self.in_think:
                # Look for earliest opener or stray closer among supported tags
                next_open = -1
                open_tag_hit = None
                for t in open_tags:
                    pos = buf.find(t, i)
                    if pos != -1 and (next_open == -1 or pos < next_open):
                        next_open = pos
                        open_tag_hit = t

                next_close = -1
                close_tag_hit = None
                for t in close_tags:
                    pos = buf.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_tag_hit = t

                if next_open == -1 and next_close == -1:
                    # Emit visible remainder, but keep only a tail that could start a tag (prefix of opener/closer)
                    keep_from = n
                    # Find last '<' to check potential tag prefix
                    last_lt = buf.rfind('<', i)
                    if last_lt != -1:
                        candidate = buf[last_lt:n]
                        # If candidate is a prefix of any tag, keep it as carry
                        if any(t.startswith(candidate) for t in open_tags + close_tags):
                            keep_from = last_lt

                    if i < keep_from:
                        out.append(buf[i:keep_from])
                    self._carry = buf[keep_from:] if keep_from < n else ""
                    return ("PASS", ''.join(out))

                # If a stray closer appears before any opener, skip the tag
                if next_close != -1 and (next_open == -1 or next_close < next_open):
                    out.append(buf[i:next_close])
                    i = next_close + close_lens[close_tag_hit]
                    continue

                # Handle opener
                if next_open != -1 and (next_close == -1 or next_open <= next_close):
                    out.append(buf[i:next_open])
                    self.in_think = True
                    self._emitted_placeholder = False
                    i = next_open + open_lens[open_tag_hit]
                    continue

                # Fallback safety
                out.append(buf[i:])
                i = n
                break
            else:
                # We're inside a think block: emit placeholder once, drop content until close
                if not self._emitted_placeholder:
                    out.append(self.think_placeholder)
                    self._emitted_placeholder = True

                # Find next closing tag among supported closers
                next_close = -1
                close_tag_hit = None
                for t in close_tags:
                    pos = buf.find(t, i)
                    if pos != -1 and (next_close == -1 or pos < next_close):
                        next_close = pos
                        close_tag_hit = t
                if next_close == -1:
                    # Consume the rest of this chunk; remain in think
                    # Capture hidden segment
                    if i < n:
                        self._hidden_parts.append(buf[i:])
                    # Keep a tail to detect split closers
                    tail_len = max_tag_len - 1
                    keep_from = max(n - tail_len, i)
                    self._carry = buf[keep_from:]
                    return ("PASS", ''.join(out))
                else:
                    # Capture hidden segment before the close tag
                    if i < next_close:
                        self._hidden_parts.append(buf[i:next_close])
                    # Finish the block and continue after the close tag
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
