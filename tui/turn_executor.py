"""Turn execution helper for the Textual app."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

from core.turns import TurnOptions, TurnRunner

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from tui.widgets.chat_transcript import ChatTranscript


class TurnExecutor:
    """Runs chat turns and updates the TUI safely from worker threads."""

    def __init__(
        self,
        session,
        turn_runner: TurnRunner,
        *,
        schedule_task: Callable[[Awaitable[Any]], None],
        emit_status: Callable[..., None],
        refresh_contexts: Callable[[], None],
        call_in_app_thread: Callable[..., None],
        stream_enabled: bool,
    ) -> None:
        self.session = session
        self.turn_runner = turn_runner
        self._schedule_task = schedule_task
        self._emit_status = emit_status
        self._refresh_contexts = refresh_contexts
        self._call_in_app_thread = call_in_app_thread
        self._stream_enabled = bool(stream_enabled)

        self._chat_view: Optional["ChatTranscript"] = None
        self._active_message_id: Optional[str] = None

    # --- wiring --------------------------------------------------------
    def set_chat_view(self, chat_view: Optional["ChatTranscript"]) -> None:
        self._chat_view = chat_view
        if chat_view is None:
            self._active_message_id = None

    def set_stream_enabled(self, enabled: bool) -> None:
        self._stream_enabled = bool(enabled)

    @property
    def active_message_id(self) -> Optional[str]:
        return self._active_message_id

    def check_auto_submit(self) -> None:
        """Expose auto-submit logic for command flows."""
        self._check_auto_submit()

    # --- public entrypoint --------------------------------------------
    async def run(self, message: str) -> None:
        if self._chat_view:
            self._active_message_id = self._chat_view.add_message(
                "assistant",
                "",
                streaming=self._stream_enabled,
            )
        else:
            self._active_message_id = None

        def _run_turn() -> Any:
            options = TurnOptions(stream=self._stream_enabled, suppress_context_print=True)
            return self.turn_runner.run_user_turn(message, options=options)

        try:
            result = await asyncio.to_thread(_run_turn)
        except Exception as exc:  # pragma: no cover - best-effort safety
            self._call_in_app_thread(self._handle_turn_error, str(exc))
            return

        self._call_in_app_thread(self._finish_turn, self._active_message_id, result)

    # --- internal helpers ---------------------------------------------
    def _finish_turn(self, msg_id: Optional[str], result: Any) -> None:
        if msg_id and self._chat_view:
            text = (result.last_text or "").strip() if hasattr(result, "last_text") else ""
            self._chat_view.update_message(msg_id, text or "(no response)", streaming=False)
        self._active_message_id = None
        self._emit_status("Assistant ready.", "debug", display=False)
        self._refresh_contexts()
        self._check_auto_submit()

    def _handle_turn_error(self, error_text: str) -> None:
        if self._chat_view and self._active_message_id:
            self._chat_view.update_message(
                self._active_message_id,
                f"Error: {error_text}",
                streaming=False,
            )
        self._emit_status(f"Error: {error_text}", "error", display=False)
        self._active_message_id = None

    def _check_auto_submit(self) -> None:
        try:
            if self.session.get_flag("auto_submit"):
                self._schedule_task(self.run(""))
        except Exception:
            pass
