"""Chat transcript widget for the Textual TUI."""

from __future__ import annotations

import uuid
from typing import Iterable, List, Optional

from rich.console import Group
from rich.padding import Padding
from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.align import Align
from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog

from tui.models import CodeBlockSpan, Msg
from tui.utils.code_block_indexer import CodeBlockIndexer


class ChatTranscript(RichLog):
    """Scrollable transcript with cursor-driven navigation."""

    ROLE_STYLES = {
        "user": ("You", "bold #90c6dd"),
        "assistant": ("Assistant", "bold #81e1c1"),
        "system": ("System", "bold #c0a4c9"),
        "tool": ("Tool", "bold #9bc6bb"),
        "command": ("Command", "bold #ffc875"),
    }

    DEFAULT_PAGE_JUMP = 5
    WINDOW_RADIUS = 200

    def __init__(
        self,
        *args,
        role_titles: Optional[dict[str, str]] = None,
        role_styles: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("markup", False)
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("auto_scroll", True)
        kwargs.setdefault("min_width", 1)
        super().__init__(*args, **kwargs)
        self.messages: List[Msg] = []
        # Maintain legacy name for OutputBridge compatibility
        self.entries = self.messages

        self._role_titles = role_titles or {}
        self._role_styles = role_styles or {}
        self._cursor: Optional[int] = None
        self._follow_latest: bool = True
        self._page_jump = self.DEFAULT_PAGE_JUMP
        self._dirty_messages = {}
        self._flush_pending = False

    # ------------------------------------------------------------------
    # Message lifecycle
    def add_message(self, role: str, text: str, *, streaming: bool = False) -> str:
        message = self._make_message(role or "assistant", text, streaming)
        self.messages.append(message)
        if self._follow_latest or self._cursor is None:
            self._cursor = len(self.messages) - 1
            self._follow_latest = True
        self._render_messages()
        return message.msg_id

    def _make_message(self, role, text, streaming=False):
        message = Msg(msg_id=uuid.uuid4().hex, role=role, text=text or "", streaming=streaming)
        self._prepare_message(message)
        return message

    def append_history(self, messages):
        """Restore saved messages in a single layout pass."""
        self.messages.extend(self._make_message(row['role'], row['text']) for row in messages)
        self._cursor = len(self.messages) - 1 if self.messages else None
        self._follow_latest = self.auto_scroll = True
        self._render_messages()

    def insert_message_before(self, before_id: str, role: str, text: str) -> str:
        message = self._make_message(role or "system", text)
        insert_at = self._find_index(before_id)
        if insert_at is None:
            insert_at = len(self.messages)
        self.messages.insert(insert_at, message)
        if self._cursor is not None and insert_at <= self._cursor:
            self._cursor += 1
        self._render_messages()
        return message.msg_id

    def update_message(self, msg_id: str, text: str, *, streaming: Optional[bool] = None) -> None:
        idx = self._find_index(msg_id)
        if idx is None:
            return
        message = self.messages[idx]
        self._dirty_messages.pop(msg_id, None)
        message.text = text or ""
        if streaming is not None:
            message.streaming = bool(streaming)
        self._prepare_message(message)
        if not message.streaming and self._follow_latest:
            self._cursor = len(self.messages) - 1
        self._render_messages()

    def append_text(self, msg_id: str, chunk: str) -> None:
        idx = self._find_index(msg_id)
        if idx is None:
            return
        message = self.messages[idx]
        message.text = (message.text or "") + (chunk or "")
        message.streaming = True
        if self._follow_latest:
            self._cursor = len(self.messages) - 1
        # Render a burst of deltas together. The authoritative final message can
        # arrive before this callback; it already has the correct Markdown.
        if self.is_mounted:
            self._dirty_messages[msg_id] = message
            if not self._flush_pending:
                self._flush_pending = True
                self.set_timer(1 / 30, self._flush_stream)
        else:
            self._prepare_message(message)
            self._render_messages()

    def _flush_stream(self):
        self._flush_pending = False
        if self._dirty_messages:
            for message in self._dirty_messages.values():
                self._prepare_message(message)
            self._dirty_messages.clear()
            self._render_messages()

    def stop_streaming(self, msg_id: str) -> None:
        idx = self._find_index(msg_id)
        if idx is None:
            return
        message = self.messages[idx]
        self._dirty_messages.pop(msg_id, None)
        message.streaming = False
        self._prepare_message(message)
        self._render_messages()

    def on_resize(self, event):
        if event.size.width and event.size.width != getattr(self, '_render_width', None):
            self._render_width = event.size.width
            self.call_after_refresh(self._render_messages)

    def clear_messages(self) -> None:
        self._dirty_messages.clear()
        self.messages.clear()
        self._cursor = None
        self._follow_latest = True
        self._render_messages()

    def set_role_customization(
        self,
        role_titles: Optional[dict[str, str]] = None,
        role_styles: Optional[dict[str, str]] = None,
    ) -> None:
        self._role_titles = dict(role_titles or {})
        self._role_styles = dict(role_styles or {})
        self._render_messages()

    # ------------------------------------------------------------------
    # Cursor management
    def current_message(self) -> Optional[Msg]:
        if self._cursor is None:
            return None
        if 0 <= self._cursor < len(self.messages):
            return self.messages[self._cursor]
        return None

    def select_message(self, msg_id):
        index = self._find_index(msg_id)
        if index is not None:
            self._cursor = index
            self._follow_latest = False
            self.auto_scroll = False
            self._render_messages()

    def move_cursor(self, delta: int) -> Optional[Msg]:
        if not self.messages:
            return None
        if delta == 0:
            return self.current_message()
        index = self._cursor if self._cursor is not None else len(self.messages) - 1
        index = max(0, min(len(self.messages) - 1, index + delta))
        self._follow_latest = index == len(self.messages) - 1 and delta > 0
        self._cursor = index
        self.auto_scroll = False
        self._render_messages()
        return self.current_message()

    def page_cursor(self, direction: int) -> Optional[Msg]:
        delta = self._page_jump * (1 if direction >= 0 else -1)
        return self.move_cursor(delta)

    def move_cursor_role(self, role: str, direction: int) -> Optional[Msg]:
        if not self.messages:
            return None
        if role not in {"assistant", "user", "system", "tool", "command"}:
            return self.current_message()
        start = self._cursor if self._cursor is not None else len(self.messages) - 1
        if direction >= 0:
            indices = range(start + 1, len(self.messages))
        else:
            indices = range(start - 1, -1, -1)
        for idx in indices:
            if self.messages[idx].role == role:
                self._cursor = idx
                self.auto_scroll = False
                self._follow_latest = idx == len(self.messages) - 1
                self._render_messages()
                return self.messages[idx]
        return self.current_message()

    def jump_home(self) -> Optional[Msg]:
        if not self.messages:
            return None
        self._cursor = 0
        self.auto_scroll = False
        self._follow_latest = False
        self._render_messages()
        return self.current_message()

    def jump_end(self) -> Optional[Msg]:
        if not self.messages:
            return None
        self._cursor = len(self.messages) - 1
        self._follow_latest = True
        self.auto_scroll = True
        self._render_messages()
        return self.current_message()

    def set_page_jump(self, amount: int) -> None:
        if amount <= 0:
            return
        self._page_jump = amount

    # ------------------------------------------------------------------
    def _prepare_message(self, message: Msg) -> None:
        message.code_blocks = CodeBlockIndexer.index(message.text or "")
        message.rich = self._render_body(message)

    def _render_body(self, message: Msg):
        if message.role == "assistant":
            try:
                return Markdown(message.text or "")
            except Exception:
                return Text(message.text or "", style="default")
        if message.role == "user":
            return Text(message.text or "", style="white")
        return Text(message.text or "", style="default")

    def _render_messages(self) -> None:
        self.clear()
        if not self.messages:
            welcome = Text()
            welcome.append('▛▚▞▜ ▛▀▀ ▛▚▞▜ ▛▀▀ ▚ ▞\n'
                           '▌  ▐ ▛▀  ▌  ▐ ▛▀   ▓ \n'
                           '▘  ▝ ▀▀▀ ▘  ▝ ▀▀▀ ▞ ▚', style='bold #81e1c1')
            welcome.append('\n\nStart a conversation', style='bold #e0ede5')
            welcome.append('\nType below, or / for commands.', style='#a4bdb6')
            self.write("")
            self.write(Align.center(welcome), expand=True)
            return

        cursor = self._cursor
        if cursor is None or cursor >= len(self.messages):
            cursor = len(self.messages) - 1
            self._cursor = cursor

        start = 0
        end = len(self.messages)
        radius = self.WINDOW_RADIUS
        if len(self.messages) > (radius * 2):
            start = max(0, cursor - radius)
            end = min(len(self.messages), cursor + radius + 1)

        for idx in range(start, end):
            message = self.messages[idx]
            entry = self._entry(message, highlighted=(idx == cursor))
            if idx == cursor:
                cursor_line = len(self.lines)
            self.write(entry, expand=True, scroll_end=False)
            if idx != end - 1:
                self.write("")

        if self._follow_latest:
            self.scroll_end(animate=False)
        else:
            self.scroll_to(y=cursor_line, animate=False)

    def _entry(self, message: Msg, *, highlighted: bool) -> Group:
        default_title, default_style = self.ROLE_STYLES.get(message.role, ("Other", "cyan"))
        title = self._role_titles.get(message.role, default_title)
        try:
            style = Style.parse(self._role_styles.get(message.role, default_style))
        except StyleSyntaxError:
            style = Style.parse(default_style)
        heading = Text('▸ ' if highlighted else '╶ ', style=style)
        heading.append(title, style='bold')
        return Group(heading, Padding(message.rich, (0, 1, 0, 2)))

    def _find_index(self, msg_id: str) -> Optional[int]:
        for idx, entry in enumerate(self.messages):
            if entry.msg_id == msg_id:
                return idx
        return None

    # Convenience for tests / tooling
    def iter_code_blocks(self) -> Iterable[CodeBlockSpan]:
        for message in self.messages:
            for block in message.code_blocks:
                yield block

    def has_streaming_messages(self) -> bool:
        return any(message.streaming for message in self.messages)
