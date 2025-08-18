from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


@dataclass
class CapabilityFlags:
    file_picker: bool = False
    rich_text: bool = False
    progress: bool = True
    diffs: bool = False
    blocking: bool = True  # True for CLI (ask_* returns), False for Web/TUI (ask_* raises)


class UI:
    """Abstract UI adapter.

    CLI implementations should block for input and return values.
    Web/TUI implementations should raise InteractionNeeded from base_classes
    when input is required.
    """

    capabilities: CapabilityFlags = CapabilityFlags()

    # Input requests -----------------------------------------------------
    def ask_text(self, prompt: str, *, default: Optional[str] = None, multiline: bool = False) -> str:
        raise NotImplementedError

    def ask_bool(self, prompt: str, *, default: Optional[bool] = None) -> bool:
        raise NotImplementedError

    def ask_choice(
        self,
        prompt: str,
        options: List[str],
        *,
        default: Optional[Union[str, List[str]]] = None,
        multi: bool = False,
    ) -> Union[List[str], str]:
        raise NotImplementedError

    def ask_files(
        self,
        prompt: str,
        *,
        accept: Optional[List[str]] = None,
        multiple: bool = True,
        must_exist: bool = True,
    ) -> List[str]:
        raise NotImplementedError

    # Fire-and-forget updates -------------------------------------------
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        raise NotImplementedError
