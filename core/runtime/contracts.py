"""Execution data shared by terminal and headless callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RunStopped(Exception):
    """An application service ends execution at a provider-call boundary."""

    def __init__(self, reason: str, message: str, *, completed: bool = False):
        super().__init__(message)
        self.reason = reason
        self.completed = completed


@dataclass(frozen=True)
class RunRequest:
    """A submitted task, including its bounded provider/tool exchanges."""

    message: str = ''
    stream: bool = False
    stdin_as_message: bool = False
    capture_raw: bool = False
    input_limit: int | None = None
    contexts: tuple[tuple[str, Any], ...] = ()
    execute_tools: bool = False
    max_calls: int = 1


@dataclass(frozen=True)
class RunEvent:
    """One ordered observation; sequence numbers are local to a run."""

    run_id: str
    session_id: str | None
    sequence: int
    timestamp: str
    type: str
    data: dict[str, Any]


@dataclass
class RunOutcome:
    """Execution result, independent of rendering or transport.

    Deltas go to observers, not the retained event list. Raw provider payloads
    are opt-in and never emitted to observers.
    """

    run_id: str
    last_text: str | None = None
    sanitized: str | None = None
    raw: Any = None
    turns: int = 0
    usage: dict | None = None
    cost: dict | None = None
    events: list[RunEvent] = field(default_factory=list)
    status: str = 'completed'
    error: str | None = None
    stop_reason: str | None = None
    truncated: bool = False
    tool_calls: int = 0
    tool_failures: int = 0


@dataclass(frozen=True)
class InteractionRequest:
    """Input needed by an action, independent of its frontend or transport."""

    id: str
    kind: str
    spec: dict[str, Any]
