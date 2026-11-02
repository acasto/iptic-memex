from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ui.base import UI, CapabilityFlags
from base_classes import InteractionNeeded


class NullUI(UI):
    """A non-interactive UI adapter for internal runs.

    - Does not print to stdout; stores last events for inspection.
    - ask_* methods raise InteractionNeeded to avoid blocking.
    """

    def __init__(self) -> None:
        # Non-blocking: callers should not expect interactive prompts
        self.capabilities = CapabilityFlags(blocking=False, progress=False)
        self.events: List[Dict[str, Any]] = []

    # Inputs -------------------------------------------------------------
    def ask_text(self, prompt: str, *, default: Optional[str] = None, multiline: bool = False) -> str:
        raise InteractionNeeded('text', {'prompt': prompt, 'default': default, 'multiline': multiline}, state_token='internal')

    def ask_bool(self, prompt: str, *, default: Optional[bool] = None) -> bool:
        raise InteractionNeeded('bool', {'prompt': prompt, 'default': default}, state_token='internal')

    def ask_choice(
        self,
        prompt: str,
        options: List[str],
        *,
        default: Optional[Union[str, List[str]]] = None,
        multi: bool = False,
    ) -> Union[List[str], str]:
        raise InteractionNeeded('choice', {'prompt': prompt, 'options': options, 'default': default, 'multi': multi}, state_token='internal')

    def ask_files(
        self,
        prompt: str,
        *,
        accept: Optional[List[str]] = None,
        multiple: bool = True,
        must_exist: bool = True,
    ) -> List[str]:
        raise InteractionNeeded('files', {'prompt': prompt, 'accept': accept, 'multiple': multiple, 'must_exist': must_exist}, state_token='internal')

    # Events -------------------------------------------------------------
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        # Store, but do not print
        self.events.append({'type': event_type, **(data or {})})

