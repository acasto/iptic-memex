"""
Abstract base classes for iptic-memex components.

These classes define the interfaces that providers, modes, contexts, and actions
must implement to work with the iptic-memex architecture.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any, Generator, Dict, List, Union


class APIProvider(ABC):
    """
    Abstract class for API handlers
    """

    @abstractmethod
    def chat(self) -> Any:
        pass

    @abstractmethod
    def stream_chat(self) -> Generator[Any, None, None]:
        pass

    @abstractmethod
    def get_messages(self) -> Any:
        pass

    @abstractmethod
    def get_full_response(self) -> Any:
        pass

    @abstractmethod
    def get_usage(self) -> Any:
        pass

    @abstractmethod
    def reset_usage(self) -> Any:
        pass

    @abstractmethod
    def get_cost(self) -> dict:
        """Calculate cost based on token usage and model pricing"""
        pass


class InteractionMode(ABC):
    """
    Abstract class for interaction handlers
    """

    @abstractmethod
    def start(self):
        pass


class InteractionContext(ABC):
    """
    Abstract class for interaction contexts
    """

    @abstractmethod
    def get(self):
        pass


class InteractionAction(ABC):
    """
    Abstract class for interaction actions
    """

    @abstractmethod
    def run(self, *args, **kwargs):
        pass


# --- Stepwise action model (backward compatible) -------------------------


@dataclass
class Completed:
    payload: Dict[str, Any]


@dataclass
class Updates:
    events: List[Dict[str, Any]]


class InteractionNeeded(Exception):
    def __init__(self, kind: str, spec: Dict[str, Any], state_token: str):
        super().__init__(kind)
        self.kind = kind
        self.spec = spec
        self.state_token = state_token


class ActionError(Exception):
    def __init__(self, user_message: str, *, recoverable: bool, debug_info: Optional[Dict[str, Any]] = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.recoverable = recoverable
        self.debug_info = debug_info or {}


class StepwiseAction(InteractionAction):
    """Optional protocol for actions to support stepwise execution.

    Default run() drives start/resume until a Completed is returned.
    In CLI, ask_* calls should not raise InteractionNeeded. In Web/TUI,
    the mode will catch InteractionNeeded higher up; run() is still
    retained for backward compatibility with existing call-sites.
    """

    def start(self, args: Dict[str, Any] | None = None, content: Any | None = None) -> Union[Completed, Updates]:
        raise NotImplementedError

    def resume(self, state_token: str, response: Any) -> Union[Completed, Updates]:
        raise NotImplementedError

    # Back-compat adapter: drive until Completed
    def run(self, *args, **kwargs):  # type: ignore[override]
        # Normalize inputs
        call_args = kwargs.get('args') if 'args' in kwargs else (args[0] if len(args) > 0 else None)
        content = kwargs.get('content') if 'content' in kwargs else (args[1] if len(args) > 1 else None)
        res = self.start(call_args or {}, content)
        # Forward non-terminal updates to UI and continue
        while True:
            if isinstance(res, Completed):
                return res
            if isinstance(res, Updates):
                try:
                    ui = getattr(self, 'session', None).ui if hasattr(self, 'session') else None
                    if ui:
                        for e in res.events:
                            ui.emit(e.get('type', 'status'), e)
                except Exception:
                    pass
                # Let the action continue if it can; '__implicit__' denotes no external response
                res = self.resume("__implicit__", {"continue": True})
                continue
            # In CLI mode, InteractionNeeded should never escape ask_*.
            # If it does, surface a recoverable error.
            raise ActionError("Interaction needed but not handled in this mode.", recoverable=True)
