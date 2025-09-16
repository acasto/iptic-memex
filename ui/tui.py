from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ui.base import UI, CapabilityFlags
from base_classes import InteractionNeeded


class TUIUI(UI):
    """TUI adapter with stepwise interactions similar to WebUI."""

    def __init__(self, session):
        self.session = session
        self.capabilities = CapabilityFlags(file_picker=True, rich_text=True, progress=True, diffs=True, blocking=False)
        self._event_handler = None

    def set_event_handler(self, handler):
        """Allow the TUI app to receive emit events."""
        self._event_handler = handler

    def _raise(self, kind: str, spec: Dict[str, Any]):
        token = spec.get('state_token') or 'UNISSUED'
        raise InteractionNeeded(kind, spec, token)

    def ask_text(self, prompt: str, *, default: Optional[str] = None, multiline: bool = False) -> str:
        self._raise('text', {'prompt': prompt, 'default': default, 'multiline': multiline})

    def ask_bool(self, prompt: str, *, default: Optional[bool] = None) -> bool:
        self._raise('bool', {'prompt': prompt, 'default': default})

    def ask_choice(
        self,
        prompt: str,
        options: List[str],
        *,
        default: Optional[Union[str, List[str]]] = None,
        multi: bool = False,
    ) -> Union[List[str], str]:
        self._raise('choice', {'prompt': prompt, 'options': options, 'default': default, 'multi': multi})

    def ask_files(
        self,
        prompt: str,
        *,
        accept: Optional[List[str]] = None,
        multiple: bool = True,
        must_exist: bool = True,
    ) -> List[str]:
        self._raise('files', {'prompt': prompt, 'accept': accept, 'multiple': multiple, 'must_exist': must_exist})

    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._event_handler:
            try:
                payload = dict(data) if isinstance(data, dict) else {'message': str(data)}
                self._event_handler(event_type, payload)
            except Exception:
                pass
