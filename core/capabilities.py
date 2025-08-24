"""Capability constants and per-call controls.

Use these in actions or internal runners to express intent without
coupling to provider-specific flags.
"""

# Primary capabilities
CHAT = "chat"
EMBEDDING = "embedding"
VISION = "vision"
TOOLS = "tools"


def disable_tools_in_overrides(overrides: dict | None) -> dict:
    """Return a copy of overrides with tool_mode forced to 'none'."""
    o = dict(overrides or {})
    o['tool_mode'] = 'none'
    return o


def exclude_images_in_overrides(overrides: dict | None) -> dict:
    """Return a copy of overrides that instructs providers to omit image parts.

    Providers that support image inputs should respect 'include_images=False'.
    """
    o = dict(overrides or {})
    o['include_images'] = False
    return o

