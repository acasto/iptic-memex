from __future__ import annotations

from typing import Any, Callable


class UIEventProxy:
    """Lightweight shim to forward UI.emit events into the TUI.

    Some UI adapters (e.g., NullUI) don't expose a `set_event_handler` hook.
    Wrapping them with this proxy lets the TUI intercept `emit()` calls and
    render status lines in the transcript without changing core UI classes.
    """

    def __init__(self, ui: Any, handler: Callable[[str, dict], None]) -> None:
        self._ui = ui
        self._handler: Callable[[str, dict], None] = handler
        # Preserve capabilities when present so call sites behave the same
        try:
            self.capabilities = getattr(ui, 'capabilities')
        except Exception:
            pass

    # --- handler management ---------------------------------------------
    def set_event_handler(self, handler: Callable[[str, dict], None]) -> None:
        self._handler = handler

    # --- delegate input methods ----------------------------------------
    def ask_text(self, *a, **kw):
        return self._ui.ask_text(*a, **kw)

    def ask_bool(self, *a, **kw):
        return self._ui.ask_bool(*a, **kw)

    def ask_choice(self, *a, **kw):
        return self._ui.ask_choice(*a, **kw)

    def ask_files(self, *a, **kw):
        return self._ui.ask_files(*a, **kw)

    # --- event forwarding ------------------------------------------------
    def emit(self, event_type: str, data: dict) -> None:
        # First forward to TUI so updates are visible
        try:
            if self._handler:
                self._handler(event_type, data or {})
        except Exception:
            pass
        # Then delegate to the underlying UI (keeps existing behavior)
        try:
            return self._ui.emit(event_type, data)
        except Exception:
            return None

    # --- generic fallback -----------------------------------------------
    def __getattr__(self, name: str) -> Any:
        # Delegate any other attributes to the wrapped UI
        return getattr(self._ui, name)

