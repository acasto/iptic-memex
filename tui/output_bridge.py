"""Helpers for routing session output into Textual widgets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from tui.output_sink import OutputEvent

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from tui.widgets.chat_transcript import ChatTranscript
    from tui.widgets.status_panel import StatusPanel


class OutputBridge:
    """Transforms background output events into TUI updates."""

    def __init__(self, *, status_limit: int = 200) -> None:
        self._status_limit = status_limit
        self._status_history: List[Tuple[str, str]] = []
        self._chat_view: Optional["ChatTranscript"] = None
        self._status_log: Optional["StatusPanel"] = None
        self._spinner_messages: Dict[str, str] = {}
        self._active_tool_message_id: Optional[str] = None
        self._active_command_message_id: Optional[str] = None
        self._last_emit: Optional[Tuple[str, str, str]] = None  # (role, level, text)

    # --- widget wiring -------------------------------------------------
    def set_chat_view(self, chat_view: Optional["ChatTranscript"]) -> None:
        self._chat_view = chat_view
        if chat_view is None:
            self._active_tool_message_id = None

    def set_status_log(self, status_log: Optional["StatusPanel"]) -> None:
        self._status_log = status_log

    # --- public helpers ------------------------------------------------
    @property
    def status_history(self) -> List[Tuple[str, str]]:
        return list(self._status_history)

    def log_status(self, text: str, level: str) -> None:
        if self._status_log:
            self._status_log.log_status(text, level)

    def record_status(self, text: str, level: str) -> None:
        self._status_history.append((text, level))
        if len(self._status_history) > self._status_limit:
            self._status_history = self._status_history[-self._status_limit :]

    def display_status_message(
        self,
        text: str,
        level: str,
        *,
        before_id: Optional[str] = None,
        role: str = "system",
    ) -> None:
        if level == "debug":
            return
        chat = self._chat_view
        if not chat:
            return
        if text and text.count("\n") > 4:
            markers = ("User:", "Assistant:", "> User:", "> Assistant:", "User ", "Assistant ")
            if any(marker in text for marker in markers):
                return
        prefix = {
            "info": "ⓘ",
            "warning": "⚠",
            "error": "❌",
            "critical": "❌",
        }.get(level, "ⓘ")
        message = f"{prefix} {text}" if text else prefix
        if before_id:
            if role == "tool" and self._active_tool_message_id:
                try:
                    chat.append_text(self._active_tool_message_id, ("\n" if chat.entries else "") + message)
                    return
                except Exception:
                    pass
            if role == "command" and self._active_command_message_id:
                try:
                    chat.append_text(self._active_command_message_id, ("\n" if chat.entries else "") + message)
                    return
                except Exception:
                    pass
            inserted_id = chat.insert_message_before(before_id, role, message)
            if role == "tool":
                self._active_tool_message_id = inserted_id
            if role == "command":
                self._active_command_message_id = inserted_id
        else:
            if role == "tool":
                if self._active_tool_message_id:
                    try:
                        chat.append_text(self._active_tool_message_id, "\n" + message)
                        return
                    except Exception:
                        pass
                try:
                    entries = getattr(chat, 'entries', []) or []
                    for i in range(len(entries) - 1, -1, -1):
                        e = entries[i]
                        if getattr(e, 'role', None) == 'assistant':
                            inserted_id = chat.insert_message_before(e.msg_id, role, message)
                            self._active_tool_message_id = inserted_id
                            return
                except Exception:
                    pass
            if role == "command":
                if self._active_command_message_id:
                    try:
                        chat.append_text(self._active_command_message_id, "\n" + message)
                        return
                    except Exception:
                        pass
                # Try to reuse the most recent command bubble if present
                try:
                    entries = getattr(chat, 'entries', []) or []
                    for i in range(len(entries) - 1, -1, -1):
                        e = entries[i]
                        if getattr(e, 'role', None) == 'command':
                            # Append instead of creating a new bubble
                            chat.append_text(e.msg_id, "\n" + message)
                            self._active_command_message_id = e.msg_id
                            return
                except Exception:
                    pass
                # Otherwise create a new command bubble at the end
                new_id = chat.add_message(role, message)
                self._active_command_message_id = new_id
                return
            new_id = chat.add_message(role, message)
            if role == "tool":
                self._active_tool_message_id = new_id

    # --- event handling ------------------------------------------------
    def handle_output_event(self, event: OutputEvent, active_message_id: Optional[str]) -> None:
        if event.type == "write":
            text = event.text or ""
            if event.is_stream and active_message_id and self._chat_view:
                self._active_tool_message_id = None
                self._chat_view.append_text(active_message_id, text)
                return
            stripped = text.rstrip("\n")
            if not stripped:
                return
            level = event.level or "info"
            is_tool = (event.origin or "").lower() == "tool"
            is_command = (event.origin or "").lower() == "command"
            role = "tool" if is_tool else "system"
            if is_command:
                role = "command"
            before_id = active_message_id if (is_tool and active_message_id) else None
            self.record_status(stripped, level)
            self.display_status_message(
                stripped,
                level,
                before_id=before_id,
                role=role,
            )
            self.log_status(stripped, level)
            return

        if event.type == "spinner":
            label = event.text or "Working..."
            self.record_status(label, "info")
            self.log_status(label, "info")
            if event.spinner_id:
                self._spinner_messages[event.spinner_id] = label
            # Surface tool spinner messages inline when streaming toward an assistant bubble
            if label.startswith("Tool calling:") and active_message_id:
                self.display_status_message(
                    label,
                    "info",
                    before_id=active_message_id,
                    role="tool",
                )
            return

        if event.type == "spinner_done":
            label = self._spinner_messages.pop(event.spinner_id, None)
            if label:
                message = f"{label} – done"
                self.record_status(message, "debug")
                self.log_status(message, "debug")

    def handle_ui_event(self, event_type: str, data: Dict[str, Any]) -> None:
        message = str(data.get("message") or data.get("text") or "")
        if event_type == "progress":
            progress = data.get("progress")
            if progress is not None:
                pct = int(float(progress) * 100)
                if message:
                    message = f"{message} ({pct}%)"
                else:
                    message = f"Progress {pct}%"
        if not message:
            message = str(data)
        level = "info"
        if event_type in {"warning", "error", "critical"}:
            level = event_type
        self.record_status(message, level)
        self.display_status_message(message, level)
        self.log_status(message, level)

    # --- cleanup -------------------------------------------------------
    def reset(self) -> None:
        self._spinner_messages.clear()
        self._status_history.clear()
        self._active_tool_message_id = None
        self._active_command_message_id = None

    # Public helper to end a command group explicitly
    def end_command_group(self) -> None:
        self._active_command_message_id = None
