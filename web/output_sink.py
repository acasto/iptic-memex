from __future__ import annotations

"""
WebOutput: a minimal output sink compatible with utils.output interface methods,
designed to capture writes for web streaming instead of printing to stdout.

MVP: Provides no-op spinner and styles; accumulates text into an internal buffer.
In a later step, this can be extended to push tokens into an asyncio.Queue for SSE.
"""

from typing import Any, Dict, List, Optional, Union
from contextlib import contextmanager
import asyncio


class WebOutput:
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None, queue: Optional[asyncio.Queue] = None) -> None:
        self._buffer: List[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = loop
        self._queue: Optional[asyncio.Queue] = queue
        self._emitted: bool = False
        # Optional cooperative cancellation hook: a callable returning True when cancel is requested
        self.cancel_check = None
        self._scope_stack: List[Dict[str, Any]] = []

    def set_async(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue

    def _emit_token(self, text: str) -> None:
        if not self._loop or not self._queue:
            return
        try:
            payload: Dict[str, Any] = {"type": "token", "text": text}
            scope = self.current_tool_scope()
            if scope:
                if scope.get('origin'):
                    payload['origin'] = scope.get('origin')
                if scope.get('tool_name'):
                    payload['tool'] = scope.get('tool_name')
                if scope.get('tool_call_id'):
                    payload['tool_call_id'] = scope.get('tool_call_id')
                title = scope.get('title') or scope.get('tool_title')
                if title:
                    payload['title'] = title
            self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)
            self._emitted = True
        except Exception:
            pass

    # --- core API ---
    def write(
            self,
            message: Any = '',
            level: Any = None,
            style: Any = None,
            prefix: Optional[str] = None,
            end: str = '\n',
            flush: bool = False,
            spacing: Optional[Union[int, list[int]]] = None
    ) -> None:
        """Capture output for web streaming, suppressing DEBUG-level messages."""
        # Suppress explicit DEBUG-level writes
        try:
            if level is not None:
                # Accept either an Enum with .name or a string-like value
                name = getattr(level, 'name', None)
                name = name if isinstance(name, str) else str(level)
                if name and 'DEBUG' in name.upper():
                    return
        except Exception:
            # If anything goes wrong determining level, fall through and write
            pass

        text = str(message)
        if prefix:
            text = f"{prefix}: {text}"
        # Ignore styles and spacing for web sink; just capture raw
        # For streaming tokens, emit immediately; still keep a buffer copy
        self._buffer.append(text + (end or ''))
        if text:
            self._emit_token(text)

    # level helpers
    def debug(self, message: Any, **kwargs) -> None:
        """Do not surface DEBUG messages in web output."""
        return

    def info(self, message: Any, **kwargs) -> None:
        self.write(message, **kwargs)

    def warning(self, message: Any, **kwargs) -> None:
        self.write(message, **kwargs)

    def error(self, message: Any, **kwargs) -> None:
        self.write(message, **kwargs)

    def critical(self, message: Any, **kwargs) -> None:
        self.write(message, **kwargs)

    # style helper used by ChatMode prompt label
    @staticmethod
    def style_text(
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
        # For web, return plain text; actual styling happens in the browser
        return text

    # spinner support (no-op for web)
    @contextmanager
    def spinner(self, message: str = "", style: Optional[str] = None):  # noqa: D401
        yield self

    def stop_spinner(self) -> None:
        pass

    # stdout suppression helper used in Agent Mode; return a no-op context
    @contextmanager
    def suppress_stdout_blanks(self, suppress_blank_lines: bool = True, collapse_bursts: bool = True):  # noqa: D401
        yield self

    # utility to retrieve and clear captured content
    def pop_text(self) -> str:
        text = ''.join(self._buffer)
        self._buffer.clear()
        return text

    class _ToolScopeContext:
        def __init__(
            self,
            outer: 'WebOutput',
            meta: Dict[str, Any],
            *,
            autostart: bool = False,
            autoend: bool = False,
        ) -> None:
            self._outer = outer
            self._meta = meta
            self._autostart = autostart
            self._autoend = autoend
            self._entered = False

        def __enter__(self) -> 'WebOutput._ToolScopeContext':
            if not self._entered:
                self._outer._scope_stack.append(self._meta)
                self._entered = True
                if self._autostart:
                    title = self._meta.get('title') or self._meta.get('tool_name')
                    if title:
                        self._outer.info(title)
            return self

        def status(self, message: str, *, level: str = 'info', **kwargs: Any) -> None:
            level = (level or 'info').lower()
            if level == 'debug':
                self._outer.debug(message, **kwargs)
            elif level == 'warning':
                self._outer.warning(message, **kwargs)
            elif level == 'error':
                self._outer.error(message, **kwargs)
            elif level == 'critical':
                self._outer.critical(message, **kwargs)
            else:
                self._outer.info(message, **kwargs)

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            if self._entered and self._outer._scope_stack:
                self._outer._scope_stack.pop()
            if self._autoend and exc_type is None:
                title = self._meta.get('title') or self._meta.get('tool_name')
                if title:
                    self._outer.info(f"Completed: {title}")

    def tool_scope(
        self,
        name: str,
        call_id: Optional[str] = None,
        *,
        title: Optional[str] = None,
        autostart: bool = False,
        autoend: bool = False,
    ) -> 'WebOutput._ToolScopeContext':
        meta = {
            'origin': 'tool',
            'tool_name': name,
            'tool_call_id': call_id,
            'title': title,
            'tool_title': title,
        }
        return WebOutput._ToolScopeContext(self, meta, autostart=autostart, autoend=autoend)

    def current_tool_scope(self) -> Optional[Dict[str, Any]]:
        return self._scope_stack[-1] if self._scope_stack else None
