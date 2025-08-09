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

    # Required by InteractionAction, not used for this filter
    def run(self, *args, **kwargs):  # pragma: no cover - not invoked
        return None

    def configure(self, opts: dict):
        self.tool_placeholder = opts.get('tool_placeholder', self.tool_placeholder)

    def on_complete(self):  # noqa: D401
        self.in_block = False
        self.current_label = None
        self._emitted_placeholder = False

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

        out = []
        i = 0
        n = len(text)

        while i < n:
            if not self.in_block:
                # Search for next opener-like pattern %%...%%
                start = text.find(self.OPEN_DELIM, i)
                if start == -1:
                    out.append(text[i:])
                    break

                # Append text before opener candidate
                out.append(text[i:start])

                # Find the closing %% of the opener
                end = text.find(self.OPEN_DELIM, start + 2)
                if end == -1:
                    # No full opener available; leave remainder as-is
                    out.append(text[start:])
                    break

                label = text[start + 2:end]
                label_stripped = label.strip()

                # Treat %%END%% outside a block as a stray close; strip it
                if label_stripped == 'END':
                    i = end + 2
                    continue

                # Enter a tool block
                self.in_block = True
                self.current_label = label_stripped or 'tool'
                self._emitted_placeholder = False
                i = end + 2
                # Continue in-block processing within the same chunk
                continue

            else:
                # Inside a tool block: emit once, then drop until %%END%%
                if not self._emitted_placeholder:
                    out.append(self._emit_placeholder())
                    self._emitted_placeholder = True

                close_pos = text.find(self.CLOSE_TOKEN, i)
                if close_pos == -1:
                    # Consume rest; remain in block
                    i = n
                else:
                    # Exit the block and continue output after END token
                    i = close_pos + len(self.CLOSE_TOKEN)
                    self.in_block = False
                    self.current_label = None
                    self._emitted_placeholder = False
                    # Continue scanning for potential subsequent openers
                    continue

        return ("PASS", ''.join(out))
