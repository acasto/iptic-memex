"""Startup memory templates backed by ordinary, agent-editable Markdown files."""

import re

from base_classes import InteractionAction
from core.memory_files import memory_directory, memory_enabled, read_memory


class PromptTemplateMemoryFileAction(InteractionAction):
    """Resolve memory placeholders without loading the SQLite memory tool."""

    def __init__(self, session):
        self.session = session

    def run(self, content=None):
        """Insert global/project entry files and the absolute memory directory."""
        if not content:
            return ''
        enabled = memory_enabled(self.session)
        result = str(content).replace('{{memory_directory}}',
                                      str(memory_directory(self.session)) if enabled else '')

        def replace(match):
            if not enabled:
                return ''
            project = match.group(1)
            try:
                return read_memory(self.session, project.strip() if project else None)
            except (OSError, UnicodeError, ValueError) as exc:
                self.session.ui.emit('warning', {'message': f'Could not load file memory: {exc}'})
                return ''

        return re.sub(r'\{\{memory(?::([^}]+))?\}\}', replace, result)
