from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import itertools
import uuid

from utils.output_utils import OutputLevel


@dataclass
class OutputEvent:
    """Event emitted by TuiOutput to drive UI updates."""

    type: str
    text: str | None = None
    level: str | None = None
    flush: bool = False
    is_stream: bool = False
    spacing: Optional[List[int]] = None
    spinner_id: Optional[str] = None


class TuiOutput:
    """Capture session output and forward it to the Textual app."""

    def __init__(self, on_event: Callable[[OutputEvent], None]) -> None:
        self._on_event = on_event
        self._spinner_stack: List[str] = []
        # Some CLI output callers pass OutputLevel enums; default to INFO
        self._level_cache = {
            OutputLevel.DEBUG: 'debug',
            OutputLevel.INFO: 'info',
            OutputLevel.WARNING: 'warning',
            OutputLevel.ERROR: 'error',
            OutputLevel.CRITICAL: 'critical',
        }

    # ----- helpers -----------------------------------------------------
    def _emit(self, event: OutputEvent) -> None:
        try:
            self._on_event(event)
        except Exception:
            pass

    def _coerce_level(self, level: Any) -> str:
        if isinstance(level, OutputLevel):
            return self._level_cache.get(level, 'info')
        try:
            if level is None:
                return 'info'
            if isinstance(level, str):
                return level.lower()
            name = getattr(level, 'name', None)
            if isinstance(name, str):
                return name.lower()
        except Exception:
            pass
        return 'info'

    def _normalize_spacing(self, spacing: Any) -> Optional[List[int]]:
        if spacing is None:
            return None
        try:
            if isinstance(spacing, int):
                return [spacing, spacing]
            if isinstance(spacing, (list, tuple)):
                items = list(spacing)
                if not items:
                    return None
                if len(items) == 1:
                    return [int(items[0]), int(items[0])]
                return [int(items[0] or 0), int(items[1] or 0)]
        except Exception:
            return None
        return None

    # ----- core API ----------------------------------------------------
    def write(
        self,
        message: Any = '',
        level: Any = OutputLevel.INFO,
        style: Any | None = None,
        prefix: str | None = None,
        end: str = '\n',
        flush: bool = False,
        spacing: Any | None = None,
    ) -> None:
        spacing_norm = self._normalize_spacing(spacing)
        text = '' if message is None else str(message)
        if prefix:
            text = f"{prefix}{text}"

        event = OutputEvent(
            type='write',
            text=text + ('' if end is None else end),
            level=self._coerce_level(level),
            flush=bool(flush),
            spacing=spacing_norm,
            is_stream=(end == ''),
        )
        self._emit(event)

    def debug(self, message: Any, **kwargs: Any) -> None:
        self.write(message, level=OutputLevel.DEBUG, **kwargs)

    def info(self, message: Any, **kwargs: Any) -> None:
        self.write(message, level=OutputLevel.INFO, **kwargs)

    def warning(self, message: Any, **kwargs: Any) -> None:
        self.write(message, level=OutputLevel.WARNING, **kwargs)

    def error(self, message: Any, **kwargs: Any) -> None:
        self.write(message, level=OutputLevel.ERROR, **kwargs)

    def critical(self, message: Any, **kwargs: Any) -> None:
        self.write(message, level=OutputLevel.CRITICAL, **kwargs)

    def style_text(
        self,
        text: str,
        fg: Optional[str] = None,
        bg: Optional[str] = None,
        bold: bool = False,
        dim: bool = False,
        italic: bool = False,
        underline: bool = False,
        blink: bool = False,
        reverse: bool = False,
    ) -> str:
        # Textual widgets handle styling; return text unchanged
        return text

    @contextmanager
    def spinner(self, message: str = '', style: Optional[str] = None):
        spinner_id = str(uuid.uuid4())
        self._spinner_stack.append(spinner_id)
        self._emit(OutputEvent(type='spinner', text=message, spinner_id=spinner_id))
        try:
            yield self
        finally:
            self.stop_spinner(spinner_id)

    def stop_spinner(self, spinner_id: Optional[str] = None) -> None:
        sid = spinner_id
        if not sid:
            sid = self._spinner_stack.pop() if self._spinner_stack else None
        if not sid:
            return
        self._emit(OutputEvent(type='spinner_done', spinner_id=sid))

    @contextmanager
    def suppress_stdout_blanks(self, suppress_blank_lines: bool = True, collapse_bursts: bool = True):
        yield self


__all__ = ['TuiOutput', 'OutputEvent']
