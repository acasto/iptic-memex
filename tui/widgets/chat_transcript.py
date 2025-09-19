"""Chat transcript widget for the Textual TUI."""

from __future__ import annotations

import uuid
from typing import Iterable, List, Optional

from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.widgets import RichLog

from tui.models import CodeBlockSpan, Msg
from tui.utils.code_block_indexer import CodeBlockIndexer


class ChatTranscript(RichLog):
    """Scrollable transcript with cursor-driven navigation."""

    ROLE_STYLES = {
        "user": ("You", "bold blue"),
        "assistant": ("Assistant", "bold green"),
        "system": ("System", "bold magenta"),
        "tool": ("Tool", "bold cyan"),
        "command": ("Command", "bold yellow"),
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
        super().__init__(*args, **kwargs)
        self.messages: List[Msg] = []
        # Maintain legacy name for OutputBridge compatibility
        self.entries = self.messages

        self._role_titles = role_titles or {}
        self._role_styles = role_styles or {}
        self._cursor: Optional[int] = None
        self._follow_latest: bool = True
        self._page_jump = self.DEFAULT_PAGE_JUMP

    # ------------------------------------------------------------------
    # Message lifecycle
    def add_message(self, role: str, text: str, *, streaming: bool = False) -> str:
        msg_id = uuid.uuid4().hex
        message = Msg(
            msg_id=msg_id,
            role=(role or "assistant"),
            text=text or "",
            streaming=streaming,
        )
        self._prepare_message(message)
        self.messages.append(message)
        if self._follow_latest or self._cursor is None:
            self._cursor = len(self.messages) - 1
            self._follow_latest = True
        self._render_messages()
        return msg_id

    def insert_message_before(self, before_id: str, role: str, text: str) -> str:
        msg_id = uuid.uuid4().hex
        message = Msg(
            msg_id=msg_id,
            role=(role or "system"),
            text=text or "",
        )
        self._prepare_message(message)
        insert_at = self._find_index(before_id)
        if insert_at is None:
            insert_at = len(self.messages)
        self.messages.insert(insert_at, message)
        if self._cursor is not None and insert_at <= self._cursor:
            self._cursor += 1
        self._render_messages()
        return msg_id

    def update_message(self, msg_id: str, text: str, *, streaming: Optional[bool] = None) -> None:
        idx = self._find_index(msg_id)
        if idx is None:
            return
        message = self.messages[idx]
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
        self._prepare_message(message)
        if self._follow_latest:
            self._cursor = len(self.messages) - 1
        self._render_messages()

    def stop_streaming(self, msg_id: str) -> None:
        idx = self._find_index(msg_id)
        if idx is None:
            return
        message = self.messages[idx]
        message.streaming = False
        self._prepare_message(message)
        self._render_messages()

    def clear_messages(self) -> None:
        self.messages.clear()
        self._cursor = None
        self._follow_latest = True
        self.clear()

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
            empty = Panel(
                Text("Type a message below to get started.", style="dim"),
                border_style="dim",
                box=box.ROUNDED,
                padding=(1, 2),
            )
            self.write(empty)
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
            panel = self._build_panel(idx, message, highlighted=(idx == cursor))
            self.write(panel)
            if idx != end - 1:
                self.write("")

        if self._follow_latest:
            try:
                self.scroll_end(animate=False)
            except Exception:
                pass

    def _build_panel(self, index: int, message: Msg, *, highlighted: bool) -> Panel:
        title, style = self.ROLE_STYLES.get(message.role, ("Other", "cyan"))
        title = self._role_titles.get(message.role, title)
        border_style = self._role_styles.get(message.role, style)

        padding = (1, 2)
        if message.role in {"tool", "command"}:
            padding = (0, 1)

        header = Text(f" {title} ")

        if highlighted:
            border_style = f"bold {border_style}" if "bold" not in border_style else border_style
        try:
            return Panel(
                message.rich,
                title=header,
                border_style=border_style,
                padding=padding,
                box=box.ROUNDED if not highlighted else box.HEAVY,
            )
        except Exception:
            # Fallback to a safe default if a custom style is invalid for this Rich version
            _, safe_style = self.ROLE_STYLES.get(message.role, ("Other", "cyan"))
            if highlighted and "bold" not in safe_style:
                safe_style = f"bold {safe_style}"
            return Panel(
                message.rich,
                title=header,
                border_style=safe_style,
                padding=padding,
                box=box.ROUNDED if not highlighted else box.HEAVY,
            )

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
