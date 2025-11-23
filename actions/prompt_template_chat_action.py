from __future__ import annotations

import re
from typing import Any, List

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

        if mode.startswith("last_"):
            try:
                n = int(mode.split("_", 1)[1])
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

    def _format_messages(self, msgs: List[dict]) -> str:
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
            msgs = self._get_chat_messages(spec)
            rendered = self._format_messages(msgs)
            return rendered if rendered is not None else ""

        return re.sub(self.chat_pattern, replace, text)

