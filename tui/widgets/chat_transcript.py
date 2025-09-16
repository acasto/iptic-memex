"""Chat transcript widget for the Textual TUI."""

from __future__ import annotations

import uuid
from typing import List, Optional

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich import box

from textual.widgets import RichLog

from tui.models import ChatEntry


class ChatTranscript(RichLog):
    """Scrollable transcript built on RichLog for compatibility."""

    ROLE_STYLES = {
        "user": ("You", "bold blue"),
        "assistant": ("Assistant", "bold green"),
        "system": ("System", "bold magenta"),
    }

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
        self.entries: List[ChatEntry] = []
        self._role_titles = role_titles or {}
        self._role_styles = role_styles or {}

    def add_message(self, role: str, text: str, *, streaming: bool = False) -> str:
        """Append a message to the log and return its generated identifier."""

        msg_id = uuid.uuid4().hex
        self.entries.append(
            ChatEntry(msg_id=msg_id, role=role or "assistant", text=text or "", streaming=streaming)
        )
        self._render_entries()
        return msg_id

    def update_message(self, msg_id: str, text: str, *, streaming: Optional[bool] = None) -> None:
        """Replace an existing message's content, optionally updating streaming state."""

        for idx, entry in enumerate(self.entries):
            if entry.msg_id == msg_id:
                self.entries[idx] = ChatEntry(
                    msg_id=entry.msg_id,
                    role=entry.role,
                    text=text,
                    streaming=entry.streaming if streaming is None else streaming,
                )
                break
        self._render_entries()

    def append_text(self, msg_id: str, chunk: str) -> None:
        """Append streaming text to an existing message."""

        for idx, entry in enumerate(self.entries):
            if entry.msg_id == msg_id:
                self.entries[idx] = ChatEntry(
                    msg_id=entry.msg_id,
                    role=entry.role,
                    text=(entry.text or "") + (chunk or ""),
                    streaming=True,
                )
                break
        self._render_entries()

    def clear_messages(self) -> None:
        """Clear the transcript contents."""

        self.entries.clear()
        self._render_entries()

    def _render_entries(self) -> None:
        self.clear()
        if not self.entries:
            empty = Panel(
                Text("Type a message below to get started.", style="dim"),
                border_style="dim",
                box=box.ROUNDED,
                padding=(1, 2),
            )
            self.write(empty)
            return
        for entry in self.entries:
            title, style = self.ROLE_STYLES.get(entry.role, ("Other", "cyan"))
            title = self._role_titles.get(entry.role, title)
            style = self._role_styles.get(entry.role, style)
            if entry.role == "assistant":
                try:
                    content = Markdown(entry.text or "")
                except Exception:
                    content = Text(entry.text or "", style="default")
            elif entry.role == "user":
                content = Text(entry.text or "", style="white")
            else:
                content = Text(entry.text or "", style="default")
            panel = Panel(content, title=title, border_style=style, padding=(1, 2), box=box.ROUNDED)
            self.write(panel)
            self.write("")
