from __future__ import annotations

from typing import Any, Optional

from base_classes import InteractionAction
from component_registry import PromptResolver


class BuildTurnPromptAction(InteractionAction):
    """
    Resolve and template a per-turn prompt/status snippet.

    Source precedence:
    1) Model-level `turn_prompt`
    2) Provider-level `turn_prompt`
    3) DEFAULT `turn_prompt`

    Each source is resolved via PromptResolver (chains/files/literals) and then
    passed through the configured template handlers so placeholders like
    `{{message_id}}` or `{{turn:id}}` can be filled from session/turn metadata.
    """

    def __init__(self, session):
        self.session = session
        # Build a resolver using the session config (merged base + user)
        self._resolver = PromptResolver(self.session.config)

    # ---- helpers ---------------------------------------------------------
    def _normalize_source(self, value: Any) -> Optional[str]:
        """Normalize raw config value into a usable prompt source."""
        if value is None:
            return None

        # Booleans: False disables; True is treated as "no explicit source"
        if isinstance(value, bool):
            return None if value is False else None

        try:
            s = str(value)
        except Exception:
            return None
        s = s.strip()
        if not s:
            return None
        if s.lower() in ("false", "none"):
            return None
        return s

    def _get_effective_source(self) -> Optional[str]:
        """Determine the effective turn_prompt source from model/provider/default."""
        # Model-level
        model_name = None
        try:
            model_name = self.session.params.get("model")
        except Exception:
            model_name = None
        if model_name:
            try:
                model_val = self.session.get_option_from_model("turn_prompt", model_name)
            except Exception:
                model_val = None
            src = self._normalize_source(model_val)
            if src:
                return src

        # Provider-level
        provider_name = None
        try:
            provider_name = self.session.params.get("provider")
        except Exception:
            provider_name = None
        if provider_name:
            try:
                prov_val = self.session.get_option_from_provider("turn_prompt", provider_name)
            except Exception:
                prov_val = None
            src = self._normalize_source(prov_val)
            if src:
                return src

        # DEFAULT-level
        try:
            default_val = self.session.get_option("DEFAULT", "turn_prompt", fallback=None)
        except Exception:
            default_val = None
        return self._normalize_source(default_val)

    def _process_templates(self, content: str) -> str:
        """Apply configured template handlers to the content."""
        if not isinstance(content, str) or not content:
            return ""

        try:
            template_handlers = self.session.get_option("DEFAULT", "template_handler", fallback="none")
        except Exception:
            template_handlers = "none"
        if not isinstance(template_handlers, str):
            try:
                template_handlers = str(template_handlers)
            except Exception:
                template_handlers = "none"
        if template_handlers.lower() in ("none", "false"):
            return content

        result = content
        for handler_name in (h.strip() for h in template_handlers.split(",")):
            if not handler_name:
                continue
            if handler_name.lower() == "default":
                handler_name = "prompt_template"
            handler = self.session.get_action(handler_name)
            if handler:
                try:
                    result = handler.run(result)
                except Exception:
                    # Best-effort; keep last successful result
                    continue
        return result

    # ---- public API ------------------------------------------------------
    def run(self, meta: Optional[dict] = None) -> str:
        """
        Build the turn prompt text for the current turn.

        Args:
            meta: Optional metadata dict for this turn (role, kind, id, index, etc.)
                  This is not required but allows future handlers to use it.
        """
        # Make per-turn metadata visible to template handlers via user_data
        if isinstance(meta, dict):
            try:
                self.session.set_user_data("__turn_meta__", meta)
            except Exception:
                pass

        source = self._get_effective_source()
        if not source:
            return ""

        try:
            resolved = self._resolver.resolve(source)
        except Exception:
            resolved = source
        if not isinstance(resolved, str) or not resolved:
            return ""

        processed = self._process_templates(resolved)
        return processed.strip()

