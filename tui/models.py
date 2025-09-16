"""Common data structures used by the TUI widgets and screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ChatEntry:
    """Small container for chat transcript entries."""

    msg_id: str
    role: str
    text: str = ""
    streaming: bool = False


@dataclass
class CommandItem:
    """Entry representing a user command in the palette."""

    title: str
    path: List[str]
    help: str = ""
    handler: Dict[str, Any] = field(default_factory=dict)

