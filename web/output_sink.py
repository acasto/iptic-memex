from __future__ import annotations

"""
WebOutput: a minimal output sink compatible with utils.output interface methods,
designed to capture writes for web streaming instead of printing to stdout.

MVP: Provides no-op spinner and styles; accumulates text into an internal buffer.
In a later step, this can be extended to push tokens into an asyncio.Queue for SSE.
"""

from typing import Any, List, Optional, Union
from contextlib import contextmanager
import asyncio


class WebOutput:
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None, queue: Optional[asyncio.Queue] = None) -> None:
        self._buffer: List[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = loop
        self._queue: Optional[asyncio.Queue] = queue

    def set_async(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue

    def _emit_token(self, text: str) -> None:
        if not self._loop or not self._queue:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, {"type": "token", "text": text})
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
        text = str(message)
        if prefix:
            text = f"{prefix}: {text}"
        # Ignore styles and spacing for web sink; just capture raw
        # For streaming tokens, emit immediately; still keep a buffer copy
        self._buffer.append(text + (end or ''))
        if text:
            self._emit_token(text)

    # level helpers
    def debug(self, message: Any, **kwargs) -> None: self.write(message, **kwargs)
    def info(self, message: Any, **kwargs) -> None: self.write(message, **kwargs)
    def warning(self, message: Any, **kwargs) -> None: self.write(message, **kwargs)
    def error(self, message: Any, **kwargs) -> None: self.write(message, **kwargs)
    def critical(self, message: Any, **kwargs) -> None: self.write(message, **kwargs)

    # style helper used by ChatMode prompt label
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
