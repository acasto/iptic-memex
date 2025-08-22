from __future__ import annotations

from typing import List, Optional

from base_classes import InteractionAction
from component_registry import PromptResolver


class BuildSystemAddendaAction(InteractionAction):
    """Compose conditional system-prompt addenda post-templating.

    Sources (resolved via PromptResolver; support chains or literal text):
    - Pseudo-tools guidance when effective tool mode is 'pseudo' and
      `[TOOLS].pseudo_tool_prompt` is set.
    - `supplemental_prompt` layered at DEFAULT, Provider, and Model scopes.

    Concatenation order (earlier items come first):
    1) Pseudo-tools
    2) DEFAULT.supplemental_prompt
    3) <Provider>.supplemental_prompt
    4) <Model>.supplemental_prompt
    """

    def __init__(self, session):
        self.session = session
        # Build a resolver using the session config (merged base + user)
        self._resolver = PromptResolver(self.session.config)

    def _resolve_prompt(self, source: Optional[str]) -> str:
        if not source:
            return ""
        try:
            resolved = self._resolver.resolve(str(source))
            return resolved.strip() if isinstance(resolved, str) else ""
        except Exception:
            return str(source)

    def run(self, content=None) -> str:
        items: List[str] = []

        # 1) Pseudo-tools guidance (only when effective mode is 'pseudo')
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
        except Exception:
            mode = 'none'
        if mode == 'pseudo':
            try:
                pseudo_src = self.session.get_option('TOOLS', 'pseudo_tool_prompt', fallback=None)
            except Exception:
                pseudo_src = None
            pseudo_text = self._resolve_prompt(pseudo_src)
            if pseudo_text:
                items.append(pseudo_text)

        # 2) DEFAULT-level supplemental
        try:
            default_sup = self.session.get_option('DEFAULT', 'supplemental_prompt', fallback=None)
        except Exception:
            default_sup = None
        default_text = self._resolve_prompt(default_sup)
        if default_text:
            items.append(default_text)

        # 3) Provider-level supplemental (current provider from params)
        try:
            provider_name = self.session.params.get('provider')
        except Exception:
            provider_name = None
        prov_text = ""
        if provider_name:
            try:
                prov_sup = self.session.get_option_from_provider('supplemental_prompt', provider_name)
            except Exception:
                prov_sup = None
            prov_text = self._resolve_prompt(prov_sup)
            if prov_text:
                items.append(prov_text)

        # 4) Model-level supplemental (current model from params)
        try:
            model_name = self.session.params.get('model')
        except Exception:
            model_name = None
        if model_name:
            try:
                model_sup = self.session.get_option_from_model('supplemental_prompt', model_name)
            except Exception:
                model_sup = None
            model_text = self._resolve_prompt(model_sup)
            if model_text:
                items.append(model_text)

        # De-duplicate while preserving order (avoids repeated supplementals when
        # the same chain/text is configured at multiple scopes)
        seen = set()
        unique: List[str] = []
        for s in items:
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            unique.append(s)
        return "\n\n".join(unique)
