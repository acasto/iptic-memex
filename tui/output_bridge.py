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

    # --- widget wiring -------------------------------------------------
    def set_chat_view(self, chat_view: Optional["ChatTranscript"]) -> None:
        self._chat_view = chat_view

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

    def display_status_message(self, text: str, level: str) -> None:
        if level == "debug":
            return
        chat = self._chat_view
        if not chat:
            return
        if text and text.count("\n") > 4 and ("User:" in text or "Assistant:" in text):
            return
        prefix = {
            "info": "ⓘ",
            "warning": "⚠",
            "error": "❌",
            "critical": "❌",
        }.get(level, "ⓘ")
        message = f"{prefix} {text}" if text else prefix
        chat.add_message("system", message)

    # --- event handling ------------------------------------------------
    def handle_output_event(self, event: OutputEvent, active_message_id: Optional[str]) -> None:
        if event.type == "write":
            text = event.text or ""
            if event.is_stream and active_message_id and self._chat_view:
                self._chat_view.append_text(active_message_id, text)
                return
            stripped = text.rstrip("\n")
            if not stripped:
                return
            level = event.level or "info"
            self.record_status(stripped, level)
            self.display_status_message(stripped, level)
            self.log_status(stripped, level)
            return

        if event.type == "spinner":
            label = event.text or "Working..."
            self.record_status(label, "info")
            self.log_status(label, "info")
            if event.spinner_id:
                self._spinner_messages[event.spinner_id] = label
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
