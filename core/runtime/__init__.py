"""UI-independent execution contract and tool-free completion runtime."""

from core.runtime.contracts import RunEvent, RunOutcome, RunRequest
from core.runtime.execution import execute

__all__ = ['RunEvent', 'RunOutcome', 'RunRequest', 'execute']
