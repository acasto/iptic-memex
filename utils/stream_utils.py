from __future__ import annotations

import time
from typing import Generator, Any, Optional, Callable


class StreamHandler:
    """
    Handles processing of streamed text content with configurable processing rules.
    """

    def __init__(self, config: Any, output_handler: Optional[Any] = None) -> None:
        """
        Initialize stream handler with user configuration.
        """
        self.config = config
        self.output = output_handler
        self.delay = float(self.config.get_option('DEFAULT', 'stream_delay', fallback=0.0))
        self.buffer_size = int(self.config.get_option('DEFAULT', 'stream_buffer', fallback=0))

    def process_stream(
            self,
            stream: Generator[str, None, None],
            on_token: Optional[Callable[[str], Any]] = None,
            on_complete: Optional[Callable[[str], Any]] = None,
            on_buffer: Optional[Callable[[str], Any]] = None,
            spinner_message: Optional[str] = None,
            spinner_style: Optional[str] = None,
            cancel_check: Optional[Callable[[], bool]] = None
    ) -> str:
        """
        Process a stream of text tokens, collecting them while allowing real-time processing.
        Shows spinner until first token arrives.

        Args:
            stream: Generator yielding text tokens
            on_token: Callback for each token (defaults to writing to output)
            on_complete: Callback for complete accumulated text
            on_buffer: Optional callback for buffer analysis
            spinner_message: Optional spinner message until first token
            spinner_style: Optional spinner style until first token
        Returns:
            Complete accumulated text
        """
        def default_token_handler(token):
            return self.output.write(token, end='', flush=True)

        def default_complete_handler(_):
            return self.output.write('', flush=True)

        if on_token is None and self.output:
            on_token = default_token_handler
        if on_complete is None and self.output:
            on_complete = default_complete_handler

        accumulated = ''
        buffer = ''

        try:
            # Use iterator to control spinner timing
            stream_iter = iter(stream)

            # Show spinner until first token, using provided style or default
            with self.output.spinner(message=spinner_message, style=spinner_style):
                first_token = next(stream_iter, None)

            if first_token:
                if on_token:
                    on_token(first_token)
                accumulated += first_token

            # Process rest of stream normally
            for token in stream_iter:
                # Cooperative cancellation point
                try:
                    if cancel_check and cancel_check():
                        raise KeyboardInterrupt()
                except Exception:
                    # If cancel_check itself errors, ignore and continue
                    pass
                if on_token:
                    on_token(token)
                accumulated += token

                # Handle buffering if configured
                if self.buffer_size > 0 and on_buffer:
                    buffer += token
                    if len(buffer) >= self.buffer_size:
                        on_buffer(buffer)
                        buffer = ''  # Clear buffer after processing

                # Apply configured delay
                if self.delay > 0:
                    time.sleep(self.delay)

            # Process any remaining buffer content
            if buffer and on_buffer:
                on_buffer(buffer)

            # Process complete text
            if on_complete:
                on_complete(accumulated)
            return accumulated

        except (KeyboardInterrupt, EOFError):
            if on_complete:
                on_complete(accumulated)
            return accumulated

    def buffer_stream(
            self,
            stream: Generator[str, None, None],
            buffer_size: int,
            on_buffer: Callable[[str], Any],
            on_complete: Optional[Callable[[str], Any]] = None
    ) -> str:
        """
        Convenience method for handling streams that need buffering.
        Accumulates tokens into a buffer of specified size before processing.
        """
        return self.process_stream(
            stream,
            on_buffer=lambda text: on_buffer(text) if len(text) >= buffer_size else None,
            on_complete=on_complete
        )

    def print_stream(
            self,
            stream: Generator[str, None, None],
            end: str = '',
            flush: bool = True
    ) -> str:
        """
        Convenience method for simple stream printing.
        Uses output handler if available, falls back to print.
        """
        if self.output:
            return self.process_stream(
                stream,
                lambda token: self.output.write(token, end=end, flush=flush)
            )
        else:
            return self.process_stream(
                stream,
                lambda token: print(token, end=end, flush=flush)
            )
