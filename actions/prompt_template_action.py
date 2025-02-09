import re
from datetime import datetime
from session_handler import InteractionAction


class PromptTemplateAction(InteractionAction):
    """Default template handler for prompt content"""

    def __init__(self, session):
        self.session = session
        self.template_pattern = r"\{\{([^}]+)\}\}"

    def _resolve_variable(self, var):
        """
        Resolve a template variable to its value.
        Returns None if variable cannot be resolved to preserve it for other handlers.
        """
        parts = var.split(":")

        # Handle simple variables first
        if len(parts) == 1:
            if var == "date":
                return datetime.now().isoformat()

            # Check session params
            value = self.session.get_params().get(var)
            if value is not None:
                return str(value)

            # Check config defaults
            value = self.session.conf.get_option("DEFAULT", var)
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
            value = self.session.conf.get_option(parts[1], parts[2], fallback=None)
            return str(value) if value is not None else None

        elif namespace == "env" and len(parts) == 2:
            import os
            value = os.environ.get(parts[1])
            return str(value) if value is not None else None

        elif namespace == "date":
            if len(parts) > 1:
                return datetime.now().strftime(":".join(parts[1:]))
            return datetime.now().isoformat()

        elif namespace == "session" and len(parts) == 2:
            value = self.session.get_params().get(parts[1])
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
