from __future__ import annotations

import re
from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterToolCallAction(InteractionAction):
    """
    Hides spans opened by %%<label>%% and closed by %%END%%.
    Emits a placeholder once per span; {name} in the placeholder expands to the label.
    """

    OPEN_DELIM = "%%"
    CLOSE_TOKEN = "%%END%%"

    # Tool blocks should not affect the returned parser input (display-only)
    AFFECTS_RETURN = False

    def __init__(self, session):
        self.session = session
        # Default to blank unless configured
        self.tool_placeholder = ""
        self.in_block = False
        self.current_label = None
        self._emitted_placeholder = False
        # Carry buffer to handle openers/closers across chunk boundaries
        self._carry = ""

    # Required by InteractionAction, not used for this filter
    def run(self, *args, **kwargs):  # pragma: no cover - not invoked
        return None

    def configure(self, opts: dict):
        self.tool_placeholder = opts.get('tool_placeholder', self.tool_placeholder)

    def on_complete(self):  # noqa: D401
        """Finalize state; do not surface partial markers.

        Returns an empty string to avoid leaking incomplete markers as visible text.
        """
        # Never emit carry: it represents partial markers (either opener or close tail)
        # Reset state
        self.in_block = False
        self.current_label = None
        self._emitted_placeholder = False
        self._carry = ""
        return ""

    def _emit_placeholder(self) -> str:
        name = self.current_label or "tool"
        try:
            return self.tool_placeholder.format(name=name)
        except Exception:
            # Fallback if placeholder template is malformed
            return f"⟦hidden:{name}⟧"

    def process_token(self, text: str) -> Decision:
        if not text:
            return ("PASS", text)

        buf = self._carry + text
        out = []
        i = 0
        n = len(buf)

        while i < n:
            if not self.in_block:
                # Find potential opener start '%%'
                start = buf.find(self.OPEN_DELIM, i)

                if start == -1:
                    # No opener → emit visible content, but keep a tail only if it could start an opener
                    # Specifically, retain a single trailing '%' if present to detect a split '%%'
                    if i < n:
                        keep_from = n
                        if buf[n - 1] == self.OPEN_DELIM[0]:  # '%'
                            keep_from = max(n - 1, i)
                        if i < keep_from:
                            out.append(buf[i:keep_from])
                        self._carry = buf[keep_from:] if keep_from < n else ""
                    return ("PASS", ''.join(out))

                # Emit visible content before opener
                out.append(buf[i:start])

                # Find closing '%%' of the opener label
                end = buf.find(self.OPEN_DELIM, start + 2)
                if end == -1:
                    # Incomplete opener; keep carry from start and finish for now
                    self._carry = buf[start:]
                    # Return what we could output so far
                    return ("PASS", ''.join(out))

                label = buf[start + 2:end]
                label_stripped = label.strip()

                # If it's %%END%% outside a block, just skip it
                if label_stripped == 'END':
                    i = end + 2
                    continue

                # Enter a block and emit placeholder once
                self.in_block = True
                self.current_label = label_stripped or 'tool'
                self._emitted_placeholder = False
                i = end + 2
                # Immediately emit placeholder upon entering the block
                if not self._emitted_placeholder:
                    out.append(self._emit_placeholder())
                    self._emitted_placeholder = True
                continue

            else:
                # We are inside a tool block: drop content until we see %%END%%
                close_pos = buf.find(self.CLOSE_TOKEN, i)
                if close_pos == -1:
                    # No close token yet. Keep only the longest suffix that is a prefix of CLOSE_TOKEN.
                    max_keep = 0
                    max_prefix = len(self.CLOSE_TOKEN) - 1
                    for k in range(max_prefix, 0, -1):
                        if n - i >= k and buf[n - k:n] == self.CLOSE_TOKEN[:k]:
                            max_keep = k
                            break
                    keep_from = max(n - max_keep, i)
                    self._carry = buf[keep_from:]
                    return ("PASS", ''.join(out))
                else:
                    # Found close; skip hidden content up to and including close token
                    i = close_pos + len(self.CLOSE_TOKEN)
                    self.in_block = False
                    self.current_label = None
                    self._emitted_placeholder = False
                    # Continue scanning for subsequent openers after the close
                    continue

        # If we fully consumed buf, clear carry
        self._carry = ""
        return ("PASS", ''.join(out))
