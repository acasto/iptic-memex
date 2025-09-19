"""Utilities for detecting code blocks within chat messages."""

from __future__ import annotations

import re
from typing import Iterable, List

from tui.models import CodeBlockSpan

_FENCE_PATTERN = re.compile(
    r"(^|\n)(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)\n(?P<body>.*?)(?:\n(?P=fence))(?:\n|$)",
    re.DOTALL,
)


class CodeBlockIndexer:
    """Extracts fenced code block spans from Markdown-like text."""

    @staticmethod
    def index(text: str) -> List[CodeBlockSpan]:
        """Return a list of code block spans contained in ``text``.

        The spans track the byte offsets within the original text so that
        selections can be mirrored in widgets such as ``TextArea``.
        """

        if not text:
            return []

        spans: List[CodeBlockSpan] = []
        for match in _FENCE_PATTERN.finditer(text):
            body = match.group("body") or ""
            if not body:
                continue
            info = (match.group("info") or "").strip() or None
            start = match.start("body")
            end = match.end("body")
            spans.append(CodeBlockSpan(start=start, end=end, language=info))
        return spans


def merge_code_blocks(existing: Iterable[CodeBlockSpan], new_blocks: Iterable[CodeBlockSpan]) -> List[CodeBlockSpan]:
    """Helper to combine and deduplicate code block spans.

    Existing spans are preserved unless their ranges collide with newly discovered
    ones, in which case the new span replaces the old entry.
    """

    merged: List[CodeBlockSpan] = list(existing)
    for block in new_blocks:
        replaced = False
        for idx, current in enumerate(merged):
            if current.start == block.start and current.end == block.end:
                merged[idx] = block
                replaced = True
                break
        if not replaced:
            merged.append(block)
    return merged

