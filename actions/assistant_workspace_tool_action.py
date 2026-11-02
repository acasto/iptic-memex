"""Action-discovery entry point for the workspace tool."""

from workspace.tools import WorkspaceToolAction


class AssistantWorkspaceToolAction(WorkspaceToolAction):
    """Expose the workspace package's tool through Memex action discovery."""
