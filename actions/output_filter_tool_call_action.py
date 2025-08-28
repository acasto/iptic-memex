from __future__ import annotations

import re
from base_classes import InteractionAction
from typing import Optional, Tuple


Decision = Optional[Tuple[str, Optional[str]]]


class OutputFilterToolCallAction(InteractionAction):
    """
    Hides spans opened by tool-style blocks and closed by %%END%%, aligning with
    assistant_commands_action parsing semantics:

    - Treat an opener only when it appears on its own line: ^\\s*%%<label>%%\\s*$
    - Ignore lines that are "quoted" (first non-space is one of ` ' ")
    - Close only on a matching standalone line: ^\\s*%%END%%\\s*$

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
        # Track fenced code sections (``` ... ```); skip hiding inside
        self._in_code_fence = False

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
        """Emit the configured placeholder, or blank if unset.

        - If tool_placeholder is an empty string or falsy, emit nothing.
        - If formatting fails, emit nothing (avoid surprising defaults).
        """
        name = self.current_label or "tool"
        if not self.tool_placeholder:
            return ""
        try:
            return self.tool_placeholder.format(name=name)
        except Exception:
            # If template is malformed, suppress placeholder rather than emitting a default token
            return ""

    def process_token(self, text: str) -> Decision:
        if not text:
            return ("PASS", text)

        # Work line-by-line to align with command parsing (opener/closer must be alone on a line)
        buf = self._carry + text
        lines = buf.splitlines(keepends=True)

        # If the last line doesn't end with a newline, keep it as carry for the next chunk
        if lines and not (lines[-1].endswith("\n") or lines[-1].endswith("\r")):
            carry = lines[-1]
            proc_lines = lines[:-1]
        else:
            carry = ""
            proc_lines = lines

        out_parts = []

        for line in proc_lines:
            stripped_line = line.strip()
            # Handle code fence toggling first; always pass through fence lines
            if stripped_line.startswith('```'):
                out_parts.append(line)
                # Toggle fence state (treat any triple-backtick line as a fence delimiter)
                self._in_code_fence = not self._in_code_fence
                continue

            if not self.in_block:
                # Ignore a lone closing token outside blocks, except when in code fences
                if not self._in_code_fence and stripped_line == self.CLOSE_TOKEN:
                    continue

                # Detect a standalone opener: ^\s*%%<label>%%\s*$ (not quoted)
                # Also ignore if first non-space char is a quote/backtick
                stripped = stripped_line
                # Quick pre-check to avoid regex when clearly not a block
                if not self._in_code_fence and stripped.startswith(self.OPEN_DELIM) and stripped.endswith(self.OPEN_DELIM):
                    leading = line.lstrip()
                    if leading and leading[0] in ('`', '"', "'"):
                        out_parts.append(line)
                        continue
                    m = re.match(r'^\s*%%(.*?)%%\s*\r?\n?$', line)
                    if m:
                        label = (m.group(1) or '').strip()
                        if label and label != 'END':
                            self.in_block = True
                            self.current_label = label or 'tool'
                            self._emitted_placeholder = False
                            # Emit placeholder (once)
                            placeholder = self._emit_placeholder()
                            if placeholder:
                                # Preserve the original line ending from the opener line
                                line_end = line[len(line.rstrip('\r\n')):]
                                out_parts.append(placeholder + line_end)
                            continue

                # Not a block opener → emit as-is
                out_parts.append(line)
            else:
                # Inside a tool block: hide content until a standalone %%END%% line
                if stripped_line == self.CLOSE_TOKEN:
                    self.in_block = False
                    self.current_label = None
                    self._emitted_placeholder = False
                    # Do not emit the closing line
                    continue
                # Drop hidden content lines
                continue

        # Save carry for next chunk and return visible output
        self._carry = carry
        return ("PASS", ''.join(out_parts))
