"""Modal overlay for inspecting and copying a single chat message."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea
from textual.widgets._text_area import Selection

from tui.models import CodeBlockSpan, Msg


@dataclass
class _OverlayState:
    message: Optional[Msg] = None
    blocks: tuple[CodeBlockSpan, ...] = ()
    active_block: Optional[int] = None


class ReaderOverlay(ModalScreen[None]):
    """Textual modal that surfaces a read-only view of a transcript message."""

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("a", "select_all", "Select all", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._state = _OverlayState()
        self._text = TextArea(
            "",
            read_only=True,
            soft_wrap=True,
            id="reader_overlay_text",
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="reader_overlay"):
            yield Static("Message reader (Esc to close)", id="reader_overlay_title")
            yield self._text

    async def on_show(self) -> None:
        try:
            self.set_focus(self._text)
        except Exception:
            pass

    def load_message(self, message: Msg, *, focus_block: Optional[int] = None) -> None:
        text = message.text or ""
        blocks = tuple(message.code_blocks or [])
        self._state = _OverlayState(message=message, blocks=blocks, active_block=None)
        self._text.text = text
        self._text.cursor_location = (0, 0)
        self._text.selection = Selection(start=(0, 0), end=(0, 0))
        if focus_block is not None and blocks:
            focus = focus_block % len(blocks)
            self._state.active_block = focus
            self._select_block(focus)

    def jump_block(self, direction: int) -> bool:
        blocks = self._state.blocks
        if not blocks:
            return False
        current = self._state.active_block
        if current is None:
            current = 0 if direction >= 0 else len(blocks) - 1
        else:
            current = (current + direction) % len(blocks)
        self._state.active_block = current
        self._select_block(current)
        return True

    def action_close(self) -> None:
        self.dismiss(None)

    def action_select_all(self) -> None:
        try:
            self._text.action_select_all()
        except Exception:
            pass

    def action_prev_block(self) -> None:
        self.jump_block(-1)

    def action_next_block(self) -> None:
        self.jump_block(1)

    def get_selected_text(self) -> str:
        try:
            return self._text.selected_text or ""
        except Exception:
            return ""

    def _select_block(self, index: int) -> None:
        blocks = self._state.blocks
        if not blocks:
            return
        block = blocks[index % len(blocks)]
        start_row, start_col = self._offset_to_location(block.start)
        end_row, end_col = self._offset_to_location(block.end)
        selection = Selection(start=(start_row, start_col), end=(end_row, end_col))
        self._text.selection = selection
        self._text.cursor_location = (start_row, start_col)
        try:
            self._text.scroll_to(y=max(0, start_row - 1), animate=False, immediate=True)
        except Exception:
            pass

    def _offset_to_location(self, offset: int) -> tuple[int, int]:
        text = self._text.text
        offset = max(0, min(len(text), offset))
        row = 0
        consumed = 0
        for line in text.splitlines(keepends=True):
            line_len = len(line)
            if consumed + line_len > offset:
                return row, offset - consumed
            consumed += line_len
            row += 1
        return row, max(0, offset - consumed)
