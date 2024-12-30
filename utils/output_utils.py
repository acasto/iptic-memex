from __future__ import annotations

import sys
import os
import platform
import ctypes
import threading
import time
import itertools
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, Any, List, TextIO, Union


class OutputLevel(Enum):
    """
    Log or message output levels, in ascending order of importance.
    """
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5


@dataclass
class Style:
    """
    Container for text styling attributes.
    - fg and bg can be either a named color (e.g. "red") or a hex code (e.g. "#FF0000").
    - Other boolean attributes enable ANSI effects like bold, dim, italic, etc.
    """
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    blink: bool = False
    reverse: bool = False


class ColorSystem:
    """
    Handles ANSI color and style sequences, including (attempted) Windows support.
    Named colors map to standard ANSI codes; hex codes map to 24-bit color sequences.
    """

    # Basic named colors
    COLORS: Dict[str, int] = {
        'black': 30, 'red': 31, 'green': 32, 'yellow': 33,
        'blue': 34, 'magenta': 35, 'cyan': 36, 'white': 37,
        'gray': 90, 'bright_red': 91, 'bright_green': 92,
        'bright_yellow': 93, 'bright_blue': 94,
        'bright_magenta': 95, 'bright_cyan': 96,
        'bright_white': 97,
    }

    # ANSI style codes
    STYLES: Dict[str, int] = {
        'bold': 1, 'dim': 2, 'italic': 3, 'underline': 4,
        'blink': 5, 'reverse': 7
    }

    @staticmethod
    def enable_ansi_on_windows() -> bool:
        """
        Enables ANSI escape code support on Windows 10+ if possible.
        Returns True if successful or if not on Windows; False otherwise.
        """
        if platform.system() != 'Windows':
            return True

        try:
            kernel32 = ctypes.windll.kernel32
            # 0x0001 (ENABLE_PROCESSED_OUTPUT) | 0x0002 (ENABLE_WRAP_AT_EOL_OUTPUT)
            # 0x0004 (ENABLE_VIRTUAL_TERMINAL_PROCESSING)
            mode = 0x0001 | 0x0002 | 0x0004
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), mode)
            return True
        except Exception:
            return False

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, ...]:
        """
        Convert a hex color (e.g. "#AABBCC") to an (R, G, B) tuple of ints.
        """
        color = color.lstrip('#')
        return tuple(int(color[i:i+2], 16) for i in (0, 2, 4))

    @classmethod
    def style_text(cls, text: str, style: Style) -> str:
        """
        Apply ANSI styling to the given text.
        If no style attributes are enabled, the text is returned unchanged.
        """
        # If style is effectively empty, return as-is
        if not any((
                style.fg, style.bg, style.bold, style.dim,
                style.italic, style.underline, style.blink, style.reverse
        )):
            return text

        codes: List[str] = []

        # Foreground color
        if style.fg:
            if style.fg.startswith('#'):
                r, g, b = cls._hex_to_rgb(style.fg)
                codes.append(f"38;2;{r};{g};{b}")
            else:
                color_code = cls.COLORS.get(style.fg.lower())
                if color_code:
                    codes.append(str(color_code))

        # Background color
        if style.bg:
            if style.bg.startswith('#'):
                r, g, b = cls._hex_to_rgb(style.bg)
                codes.append(f"48;2;{r};{g};{b}")
            else:
                color_code = cls.COLORS.get(style.bg.lower())
                if color_code:
                    # Background color codes are foreground + 10
                    codes.append(str(color_code + 10))

        # Additional style toggles
        style_map = {
            'bold': style.bold,
            'dim': style.dim,
            'italic': style.italic,
            'underline': style.underline,
            'blink': style.blink,
            'reverse': style.reverse
        }
        for name, enabled in style_map.items():
            if enabled and name in cls.STYLES:
                codes.append(str(cls.STYLES[name]))

        # If we ended up with no codes, return as-is
        if not codes:
            return text

        return f"\033[{';'.join(codes)}m{text}\033[0m"


class Spinner:
    """Animated spinner for indicating progress."""

    STYLES = {
        'dots': '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏',
        'line': '|/-\\',
        'block': '▖▘▝▗',
        'arrow': '←↖↑↗→↘↓↙',
        'pulse': '• •• ••• •• •',
        'blocks': '▏▎▍▌▋▊▉█',
        'none': ''
    }

    def __init__(self, message: Optional[str] = None, delay=0.1, style=None, config=None):
        default_style = 'dots'
        if config:
            default_style = config.get_option('DEFAULT', 'spinner_style', fallback='dots').lower()
        style = style or default_style
        spinner_chars = self.STYLES.get(style, self.STYLES['dots'])
        self.enabled = bool(spinner_chars)
        self.spinner = itertools.cycle(spinner_chars) if spinner_chars else None
        self.delay = delay
        self.message = f" {message}" if message else ""  # Add space if message exists
        self.busy = False
        self.thread = None
        self._stream = sys.stdout
        self._hide_cursor = '\033[?25l'
        self._show_cursor = '\033[?25h'

    def _spin(self):
        while self.busy and self.enabled:
            char = next(self.spinner)
            # Write spinner and optional message
            self._stream.write(f"{char}{self.message}")
            self._stream.flush()
            time.sleep(self.delay)
            # Move cursor back over the spinner and message
            self._stream.write('\b' * (1 + len(self.message)))

    def __enter__(self):
        if self.enabled:
            self.busy = True
            self._stream.write(self._hide_cursor)
            self.thread = threading.Thread(target=self._spin)
            self.thread.start()
        return self

    def stop(self):
        """Stop the spinner and clean up display"""
        if self.enabled and self.busy:
            self.busy = False
            time.sleep(self.delay)
            self.thread.join()
            self._stream.write(self._show_cursor)
            # Clear spinner and message by overwriting with spaces
            self._stream.write(' ' * (1 + len(self.message)) + '\b' * (1 + len(self.message)))
            self._stream.flush()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.enabled:
            self.stop()


class DummySpinnerContext:
    """No-op context manager when spinner is not supported."""
    def __enter__(self): return self
    def __exit__(self, *args): pass


# noinspection PyTypeChecker
class OutputHandler:
    """
    Provides a structured way to print messages to the console with optional color
    and styling, respecting an overall output level (e.g. INFO, WARNING, etc.).
    """

    def __init__(self, config: Any) -> None:
        """
        Initializes the output handler with user configuration.
        Expected config usage:
          - config.get_option('DEFAULT', 'colors', fallback=True) => bool
          - config.get_option('DEFAULT', 'output_level', fallback='INFO') => str
          - config.get_option('DEFAULT', 'output_styles', fallback=None) => dict or None
        """
        self.config = config
        self._stream: TextIO = sys.stdout
        self._current_spinner = None  # Track current spinner

    # Determine if color is enabled
        self._color_enabled = (
                config.get_option('DEFAULT', 'colors', fallback=True)
                and self._supports_color()
        )
        if self._color_enabled:
            ColorSystem.enable_ansi_on_windows()

        # Determine current output level
        level_str = config.get_option('DEFAULT', 'output_level', fallback='INFO')
        try:
            self.level = OutputLevel[level_str.upper()]
        except KeyError:
            self.level = OutputLevel.INFO
            self.error(f"Invalid output level '{level_str}', using INFO")

        # Default styles for each level
        self.level_styles: Dict[OutputLevel, Style] = {
            OutputLevel.DEBUG: Style(fg='gray', dim=True),
            OutputLevel.INFO: Style(),
            OutputLevel.WARNING: Style(fg='yellow'),
            OutputLevel.ERROR: Style(fg='red', bold=True),
            OutputLevel.CRITICAL: Style(fg='red', bold=True, underline=True),
        }

        # Load (and possibly merge) custom styles from config
        self._load_custom_styles()

    @staticmethod
    def _supports_color() -> bool:
        """
        Checks if the environment is set up for color output.
        Considers NO_COLOR, TTY detection, and terminal type.
        """
        if os.environ.get('NO_COLOR'):
            return False
        if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
            return False
        term = os.environ.get('TERM', '').lower()
        if term in ('dumb', 'unknown', ''):
            return False
        return True

    @staticmethod
    def _merge_styles(base: Style, overrides: Dict[str, Any]) -> Style:
        """
        Merge overrides into a copy of base Style, returning a new Style object.
        Only applies attributes found in the Style dataclass.
        """
        merged = Style(**asdict(base))
        for attr, val in overrides.items():
            if hasattr(merged, attr):
                setattr(merged, attr, val)
        return merged

    def _load_custom_styles(self) -> None:
        """
        Load and apply custom style overrides from config if provided.
        If a custom style dict is malformed, a warning is logged.
        """
        style_config = self.config.get_option('DEFAULT', 'output_styles', fallback=None)
        if style_config and isinstance(style_config, dict):
            for level_name, style_dict in style_config.items():
                try:
                    level = OutputLevel[level_name.upper()]
                    # Merge with existing default style
                    self.level_styles[level] = self._merge_styles(
                        self.level_styles[level],
                        style_dict
                    )
                except (KeyError, TypeError) as e:
                    self.warning(f"Invalid style config for '{level_name}': {e}")

    def set_stream(self, stream: TextIO) -> None:
        """
        Switch output to a different stream (useful in testing).
        """
        self._stream = stream

    def _should_output(self, level: OutputLevel) -> bool:
        """
        Determine if a message at 'level' should be shown given the current threshold.
        """
        return level.value >= self.level.value

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
            reverse: bool = False
    ) -> str:
        """
        Public method to style text on the fly with the given attributes.
        Returns unstyled text if color is disabled.
        """
        if not self._color_enabled:
            return text

        style_obj = Style(
            fg=fg, bg=bg, bold=bold, dim=dim,
            italic=italic, underline=underline,
            blink=blink, reverse=reverse
        )
        return ColorSystem.style_text(text, style_obj)

    def write(
            self,
            message: Any = '',
            level: OutputLevel = OutputLevel.INFO,
            style: Optional[Style] = None,
            prefix: Optional[str] = None,
            end: str = '\n',
            flush: bool = False,
            spacing: Optional[Union[int, List[int]]] = None
    ) -> None:
        """
        Main method to output messages. Respects the configured output level.
        If color is enabled, applies either the provided style or the style for that level.

        Args:
            message: The message to output
            level: Output level for the message (default: OutputLevel.INFO)
            style: Optional style to apply to the message
            prefix: Optional prefix to add before the message
            end: String to append after the message (default: '\n')
            flush: Whether to force flush the output (default: False)
            spacing: Controls blank lines before and after the message.
                    If int: prints same number of lines before and after
                    If list[int]: prints [before, after] lines
                    If None: no extra spacing (default: None)
        """
        if not self._should_output(level):
            return

        msg_str = str(message)
        if prefix:
            msg_str = f"{prefix}: {msg_str}"

        # Apply style if enabled
        if self._color_enabled:
            actual_style = style or self.level_styles.get(level, Style())
            msg_str = ColorSystem.style_text(msg_str, actual_style)

        # Handle spacing before message
        if spacing is not None:
            before_spacing = spacing if isinstance(spacing, int) else spacing[0]
            after_spacing = spacing if isinstance(spacing, int) else spacing[1]

            # Print blank lines before
            for _ in range(before_spacing):
                print('', file=self._stream)

        # Print the message
        print(msg_str, end=end, file=self._stream, flush=flush)

        # Handle spacing after message (define after_spacing first)
        if spacing is not None:
            after_spacing = spacing if isinstance(spacing, int) else spacing[1]
            # Print blank lines after
            for _ in range(after_spacing):
                print('', file=self._stream, flush=flush)

    def debug(self, message: Any, **kwargs) -> None:
        """Log a DEBUG message."""
        self.write(message, level=OutputLevel.DEBUG, **kwargs)

    def info(self, message: Any, **kwargs) -> None:
        """Log an INFO message."""
        self.write(message, level=OutputLevel.INFO, **kwargs)

    def warning(self, message: Any, **kwargs) -> None:
        """Log a WARNING message."""
        self.write(message, level=OutputLevel.WARNING, **kwargs)

    def error(self, message: Any, **kwargs) -> None:
        """Log an ERROR message."""
        self.write(message, level=OutputLevel.ERROR, **kwargs)

    def critical(self, message: Any, **kwargs) -> None:
        """Log a CRITICAL message."""
        self.write(message, level=OutputLevel.CRITICAL, **kwargs)

    def status(self, message: Any, fg: str = 'cyan', **kwargs) -> None:
        """
        Output a 'status' message with a custom (cyan) color by default.
        """
        self.write(message, style=Style(fg=fg), **kwargs)

    def success(self, message: Any, **kwargs) -> None:
        """
        Output a 'success' message in green and bold by default.
        """
        self.write(message, style=Style(fg='green', bold=True), **kwargs)

    def spinner(self, message="", style=None):
        """Get a spinner context manager for showing progress."""
        if not self._supports_color() or not self._color_enabled:
            return DummySpinnerContext()
        self._current_spinner = Spinner(message, style=style, config=self.config)
        return self._current_spinner

    def stop_spinner(self):
        """Stop the current spinner if one exists"""
        if self._current_spinner:
            self._current_spinner.stop()
            self._current_spinner = None
