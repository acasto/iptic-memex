from __future__ import annotations

import re
from typing import Any, List, Tuple

from base_classes import InteractionAction


class PromptTemplateChatAction(InteractionAction):
    """Template handler for chat history snippets.

    Supports placeholders like:
    - {{chat:last}}    -> last message
    - {{chat:last_3}}  -> last 3 messages
    - {{chat:window}}  -> provider-visible window (respects context_sent)
    """

    def __init__(self, session):
        self.session = session
        self.chat_pattern = r"\{\{chat(?::([^}]+))?\}\}"

    def _parse_spec(self, spec: str) -> Tuple[str, dict]:
        """Split chat spec into base mode and modifiers.

        Example: "last_5;max_tokens=256;only=user" -> ("last_5", {"max_tokens": "256", "only": "user"})
        """
        if not spec:
            return "", {}
        parts = [p.strip() for p in spec.split(";") if p.strip()]
        base = parts[0] if parts else ""
        mods: dict = {}
        for token in parts[1:]:
            if "=" in token:
                k, v = token.split("=", 1)
                mods[k.strip().lower()] = v.strip()
        return base, mods

    def _get_chat_messages(self, mode: str) -> List[dict]:
        """Return a slice of chat messages based on the requested mode."""
        try:
            chat = self.session.get_context("chat")
        except Exception:
            chat = None
        if not chat:
            return []

        try:
            # Full history (ignores context_sent)
            all_msgs = chat.get("all") or []
        except Exception:
            all_msgs = []

        mode = (mode or "").strip().lower()
        if not mode or mode == "window":
            # Respect context_sent semantics for the default window
            try:
                window = chat.get()
                return window or []
            except Exception:
                return all_msgs

        if mode == "last":
            return all_msgs[-1:] if all_msgs else []

        # Support both legacy last_<n> and new last=<n> syntax
        if mode.startswith("last_") or mode.startswith("last="):
            try:
                n_part = mode.split("_", 1)[1] if "_" in mode else mode.split("=", 1)[1]
                n = int(n_part)
            except Exception:
                n = 1
            if n <= 0:
                n = 1
            return all_msgs[-n:] if all_msgs else []

        # Fallback: use provider-visible window if available
        try:
            window = chat.get()
            return window or all_msgs
        except Exception:
            return all_msgs

    def _filter_by_role(self, msgs: List[dict], mods: dict) -> List[dict]:
        only = mods.get("only")
        exclude = mods.get("exclude")
        if not only and not exclude:
            return msgs
        filtered = msgs
        if only:
            targets = [r.strip().lower() for r in only.split(",") if r.strip()]
            filtered = [m for m in filtered if str(m.get("role", "")).lower() in targets]
        if exclude:
            blocked = [r.strip().lower() for r in exclude.split(",") if r.strip()]
            filtered = [m for m in filtered if str(m.get("role", "")).lower() not in blocked]
        return filtered

    def _count_tokens(self, text: str) -> int:
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            return len(text.split())

    def _truncate_by_tokens(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            ids = enc.encode(text, disallowed_special=())
            if len(ids) <= max_tokens:
                return text
            return enc.decode(ids[:max_tokens])
        except Exception:
            words = text.split()
            if len(words) <= max_tokens:
                return text
            return " ".join(words[:max_tokens])

    def _format_messages(self, msgs: List[dict], mods: dict) -> str:
        """Render messages as a compact plain-text transcript."""
        if not msgs:
            return ""

        lines: List[str] = []
        for turn in msgs:
            try:
                role = str(turn.get("role") or "user").capitalize()
            except Exception:
                role = "User"
            try:
                msg = str(turn.get("message") or "")
            except Exception:
                msg = ""
            lines.append(f"{role}: {msg}")

        text = "\n".join(lines)

        # Apply modifier-driven limits
        max_chars_override = None
        try:
            if "max_chars" in mods:
                raw = mods.get("max_chars")
                if isinstance(raw, str) and raw.strip().isdigit():
                    max_chars_override = int(raw.strip())
                elif isinstance(raw, int):
                    max_chars_override = raw
        except Exception:
            max_chars_override = None

        max_tokens = None
        try:
            if "max_tokens" in mods:
                raw = mods.get("max_tokens")
                if isinstance(raw, str) and raw.strip().isdigit():
                    max_tokens = int(raw.strip())
                elif isinstance(raw, int):
                    max_tokens = raw
        except Exception:
            max_tokens = None

        # First enforce token cap if requested
        if max_tokens is not None:
            text = self._truncate_by_tokens(text, max_tokens)

        # Apply a conservative truncation limit to avoid ballooning prompts
        max_chars: int = 2000
        try:
            raw = self.session.get_option("DEFAULT", "chat_template_max_chars", fallback=max_chars)
            if isinstance(raw, int):
                max_chars = raw
            elif isinstance(raw, str) and raw.isdigit():
                max_chars = int(raw)
        except Exception:
            pass
        if max_chars_override is not None:
            max_chars = max_chars_override

        if max_chars > 0 and len(text) > max_chars:
            return text[:max_chars] + f"\n… (truncated {len(text) - max_chars} chars)"
        return text

    def run(self, content: Any = None) -> str:
        """Process chat template variables in the provided content."""
        if not content:
            return ""

        text = str(content)

        def replace(match: re.Match) -> str:
            spec = match.group(1) or ""
            base_spec, mods = self._parse_spec(spec)
            msgs = self._get_chat_messages(base_spec)
            msgs = self._filter_by_role(msgs, mods)
            rendered = self._format_messages(msgs, mods)
            return rendered if rendered is not None else ""

        return re.sub(self.chat_pattern, replace, text)
