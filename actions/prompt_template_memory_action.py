from session_handler import InteractionAction
import re


class PromptTemplateMemoryAction(InteractionAction):
    """Template handler for memory content substitution"""

    def __init__(self, session):
        self.session = session
        self.memory_pattern = r"\{\{memory(?::([^}]+))?\}\}"
        self.memory_tool = session.get_action('assistant_memory_tool')

    def _get_memories(self, project=None):
        """Retrieve memories for the given project context and format as bullet list"""
        query = "SELECT memory FROM memories"
        params = []

        if project:
            query += " WHERE project = ?"
            params.append(project)
        else:
            query += " WHERE project IS NULL"

        query += " ORDER BY timestamp ASC"

        rows = self.session.utils.storage.provider.execute(query, params)
        header = f"**Project {project} Memories:**" if project else "**Memories:**"
        memories = "\n".join(f"* {row[0]}" for row in rows)
        return f"{header}\n{memories}" if memories else ""

    def run(self, content=None):
        """Process memory template variables in the provided content"""
        if not content:
            return ""

        def replace(match):
            project = match.group(1)
            memories = self._get_memories(project)
            return memories if memories else ''

        return re.sub(self.memory_pattern, replace, content)