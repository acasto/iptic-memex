"""Common data structures used by the TUI widgets and screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from rich.console import RenderableType


@dataclass
class CodeBlockSpan:
    """Represents a fenced code block within a message."""

    start: int
    end: int
    language: Optional[str] = None


@dataclass
class Msg:
    """Message tracked in the transcript view."""

    msg_id: str
    role: Literal["user", "assistant", "system", "tool", "command"]
    text: str = ""
    streaming: bool = False
    rich: Optional[RenderableType] = None
    code_blocks: List[CodeBlockSpan] = field(default_factory=list)


@dataclass
class CommandItem:
    """Entry representing a user command in the palette."""

    title: str
    path: List[str]
    help: str = ""
    handler: Dict[str, Any] = field(default_factory=dict)


# Backwards compatibility for older imports
ChatEntry = Msg
