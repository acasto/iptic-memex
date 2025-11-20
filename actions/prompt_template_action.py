import re
from datetime import datetime
from base_classes import InteractionAction


class PromptTemplateAction(InteractionAction):
    """Default template handler for prompt content"""

    def __init__(self, session):
        self.session = session
        self.template_pattern = r"\{\{([^}]+)\}\}"

    def _get_turn_meta(self):
        """Return per-turn metadata injected by callers, if any."""
        try:
            meta = self.session.get_user_data("__turn_meta__")  # type: ignore[attr-defined]
        except Exception:
            meta = None
        return meta if isinstance(meta, dict) else {}

    def _resolve_variable(self, var):
        """
        Resolve a template variable to its value.
        Returns None if variable cannot be resolved to preserve it for other handlers.
        """
        parts = var.split(":")

        # Handle simple variables first
        if len(parts) == 1:
            if var == "date":
                return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if var == "message_id":
                turn_meta = self._get_turn_meta()
                value = turn_meta.get("id")
                return str(value) if value is not None else None

            # Check session params
            value = self.session.get_params().get(var)
            if value is not None:
                return str(value)

            # Check config defaults
            value = self.session.get_option("DEFAULT", var)
            if value is not None:
                return str(value)

            # Check environment
            import os
            value = os.environ.get(var)
            if value is not None:
                return value

            # Return None to preserve for other handlers
            return None

        # Handle namespaced variables
        namespace = parts[0].lower()

        if namespace == "config" and len(parts) == 3:
            value = self.session.get_option(parts[1], parts[2], fallback=None)
            return str(value) if value is not None else None

        elif namespace == "env" and len(parts) == 2:
            import os
            value = os.environ.get(parts[1])
            return str(value) if value is not None else None

        elif namespace == "date":
            if len(parts) > 1:
                return datetime.now().strftime(":".join(parts[1:]))
            # Use a cleaner format (no microseconds, space separator)
            return datetime.now().isoformat(sep=" ", timespec="seconds")

        elif namespace == "session" and len(parts) == 2:
            value = self.session.get_params().get(parts[1])
            return str(value) if value is not None else None

        elif namespace == "turn":
            turn_meta = self._get_turn_meta()
            if len(parts) == 2:
                key = parts[1]
                value = turn_meta.get(key)
                return str(value) if value is not None else None

        # Return None for unrecognized namespaces
        return None

    def run(self, content=None):
        """Process template variables in the provided content"""
        if content is None:
            return ""

        def replace(match):
            var = match.group(1).strip()
            value = self._resolve_variable(var)
            # Preserve the original placeholder if value couldn't be resolved
            return value if value is not None else f"{{{{{var}}}}}"

        return re.sub(self.template_pattern, replace, content)
