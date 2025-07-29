"""
Abstract base classes for iptic-memex components.

These classes define the interfaces that providers, modes, contexts, and actions
must implement to work with the iptic-memex architecture.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, Any, Generator


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