"""Helpers for copying text to the system clipboard from the TUI."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Tuple


@dataclass
class ClipboardOutcome:
    """Result metadata for a clipboard attempt."""

    success: bool
    method: str
    error: Optional[str] = None


class ClipboardHelper:
    """Encapsulates OSC-52 clipboard access with platform fallbacks."""

    def copy(self, text: str, primary: Callable[[str], None]) -> ClipboardOutcome:
        """Copy ``text`` to the clipboard, returning the attempt metadata."""

        if text is None:
            text = ""

        osc_error: Optional[str] = None
        try:
            primary(text)
            return ClipboardOutcome(True, "osc52")
        except Exception as exc:
            osc_error = str(exc)

        last_error = osc_error
        for command in self._iter_fallback_commands():
            try:
                subprocess.run(
                    command,
                    check=True,
                    input=text.encode("utf-8"),
                )
                return ClipboardOutcome(True, " ".join(command))
            except Exception as fallback_exc:
                command_str = " ".join(command)
                last_error = f"{command_str}: {fallback_exc}"

        return ClipboardOutcome(False, "none", error=last_error)

    def _iter_fallback_commands(self) -> Iterable[Tuple[str, ...]]:
        system = platform.system().lower()
        if system == "darwin":
            if shutil.which("pbcopy"):
                yield ("pbcopy",)
            return
        if system == "windows":
            yield ("powershell", "-Command", "Set-Clipboard")
            return
        # Assume Linux / BSD
        if shutil.which("wl-copy"):
            yield ("wl-copy",)
        if shutil.which("xclip"):
            yield ("xclip", "-selection", "clipboard")
