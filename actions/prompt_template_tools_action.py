from __future__ import annotations

import re
from base_classes import InteractionAction
from component_registry import PromptResolver


class PromptTemplateToolsAction(InteractionAction):
    """Template handler that injects pseudo-tool instructions conditionally.

    Replaces the placeholder `{{pseudo_tool_prompt}}` with the resolved content of
    `[TOOLS].pseudo_tool_prompt` only when tools are enabled and the effective mode
    is 'pseudo'. Otherwise replaces it with an empty string.
    """

    def __init__(self, session):
        self.session = session
        self._pattern = re.compile(r"\{\{\s*pseudo_tool_prompt\s*\}\}")

    def run(self, content=None):
        if not isinstance(content, str) or not content:
            return content or ""

        # Fast exit if no placeholder
        if not self._pattern.search(content):
            return content

        # Determine effective mode via session helper
        mode = 'none'
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
        except Exception:
            mode = 'none'

        # Gate by mode
        if mode != 'pseudo':
            return self._pattern.sub('', content)

        # Resolve the configured prompt key; fall back to empty if not present
        prompt_key = None
        try:
            prompt_key = self.session.get_option('TOOLS', 'pseudo_tool_prompt', fallback=None)
        except Exception:
            prompt_key = None

        if not prompt_key:
            return self._pattern.sub('', content)

        # Use PromptResolver with the current session config; if resolution fails,
        # PromptResolver returns the key itself, which is acceptable as a last resort.
        try:
            resolver = PromptResolver(self.session.config)
            injected = resolver.resolve(str(prompt_key)) or ''
        except Exception:
            injected = ''

        return self._pattern.sub(injected, content)

