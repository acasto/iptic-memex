from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

from base_classes import InteractionAction


class PromptTemplateFileAction(InteractionAction):
    """Template handler for including local files into prompt content.

    Syntax:
      {{file:RELATIVE_OR_ABSOLUTE_PATH}}
      {{file:path;optional=true;encoding=utf-8;max_chars=2000}}

    Notes:
    - Intended for user-authored prompt files (opt-in via template_handler chain).
    - Fails gracefully: missing/unreadable files become an empty string and a warning is emitted.
    """

    def __init__(self, session):
        self.session = session
        self._pattern = r"\{\{file:([^}]+)\}\}"
        self._warned: set[str] = set()

    def _warn_once(self, message: str) -> None:
        try:
            if message in self._warned:
                return
            self._warned.add(message)
        except Exception:
            pass
        try:
            self.session.utils.output.warning(message)
        except Exception:
            pass

    @staticmethod
    def _parse_spec(spec: str) -> Tuple[str, Dict[str, str]]:
        raw = (spec or "").strip()
        if not raw:
            return "", {}

        if ";" not in raw:
            return raw.strip(), {}

        path_part, rest = raw.split(";", 1)
        mods: Dict[str, str] = {}
        for tok in (t.strip() for t in rest.split(";")):
            if not tok:
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                mods[k.strip().lower()] = v.strip()
            else:
                mods[tok.strip().lower()] = "true"
        return path_part.strip(), mods

    @staticmethod
    def _strip_quotes(value: str) -> str:
        v = (value or "").strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            return v[1:-1]
        return v

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if not s:
            return default
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off"):
            return False
        return default

    @staticmethod
    def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if not s:
            return default
        try:
            return int(s)
        except Exception:
            return default

    def _read_text_file(
        self,
        path: str,
        *,
        encoding: str = "utf-8",
        max_chars: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> str:
        try:
            # Size-based cap first (avoids huge reads); default is unlimited unless configured.
            if max_bytes is not None and max_bytes > 0:
                try:
                    size = os.path.getsize(path)
                except Exception:
                    size = None
                if isinstance(size, int) and size > max_bytes:
                    with open(path, "rb") as f:
                        raw = f.read(max_bytes)
                    try:
                        text = raw.decode(encoding, errors="replace")
                    except Exception:
                        text = raw.decode("utf-8", errors="replace")
                    return text + f"\n… (truncated to {max_bytes} bytes)"

            # Normal path: use the app's filesystem util for consistency.
            content = None
            try:
                content = self.session.utils.fs.read_file(path, binary=False, encoding=encoding)
            except Exception:
                content = None
            if content is None:
                return ""

            text = str(content)
            if max_chars is not None and max_chars > 0 and len(text) > max_chars:
                return text[:max_chars] + f"\n… (truncated {len(text) - max_chars} chars)"
            return text
        except Exception:
            return ""

    def run(self, content: Any = None) -> str:
        if not content:
            return ""

        text = str(content)

        def replace(match: re.Match) -> str:
            spec = match.group(1) or ""
            path_part, mods = self._parse_spec(spec)
            path_raw = self._strip_quotes(path_part)
            if not path_raw:
                self._warn_once("prompt_template_file: empty path in {{file:...}}")
                return ""

            encoding = mods.get("encoding") or "utf-8"
            optional = self._to_bool(mods.get("optional"), True)
            max_chars = self._to_int(mods.get("max_chars"), None)
            max_bytes = self._to_int(mods.get("max_bytes"), None)

            expanded = os.path.expanduser(path_raw)
            resolved = expanded
            if not os.path.isabs(resolved):
                try:
                    resolved = os.path.abspath(os.path.join(os.getcwd(), resolved))
                except Exception:
                    resolved = expanded

            if not os.path.isfile(resolved):
                if not optional:
                    self._warn_once(f"prompt_template_file: file not found: {path_raw}")
                return ""

            included = self._read_text_file(
                resolved,
                encoding=encoding,
                max_chars=max_chars,
                max_bytes=max_bytes,
            )
            if included == "":
                if not optional:
                    self._warn_once(f"prompt_template_file: failed to read: {path_raw}")
                return ""
            return included

        return re.sub(self._pattern, replace, text)

