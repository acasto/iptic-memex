from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ui.base import UI, CapabilityFlags
from base_classes import InteractionNeeded


class WebUI(UI):
    """Web UI adapter.

    All ask_* calls raise InteractionNeeded with a spec describing
    the required UI and a state token (to be provided by the caller).
    The actual token issuance is expected to be implemented by the
    web layer; this class focuses on signaling only.
    """

    def __init__(self, session):
        self.session = session
        self.capabilities = CapabilityFlags(file_picker=True, rich_text=True, progress=True, diffs=True, blocking=False)

    # In a pure adapter, we cannot generate tokens; the mode/server must.
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
        # No-op here; web layer will surface updates through dedicated channels
        return
