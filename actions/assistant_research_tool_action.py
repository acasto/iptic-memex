"""Action-discovery entry point for adaptive workspace decisions."""

from workspace.research_tool import ResearchToolAction


class AssistantResearchToolAction(ResearchToolAction):
    """Expose research decisions only on an explicitly configured lead session."""
