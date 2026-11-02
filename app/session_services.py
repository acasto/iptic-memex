"""Explicit service choices for headless application sessions."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class HeadlessServices:
    """Apply workflow services before provider and prompt initialization.

    None inherits configured file memory; False disables preparation, templates,
    and the file-memory filesystem exception for this session only. Required
    tools extend the selected profile, but never override its hard blocklist.
    configure installs workflow-owned state without teaching core its shape.
    """

    file_memory: bool | None = None
    required_tools: tuple[str, ...] = ()
    configure: Callable[[Any], None] | None = None
