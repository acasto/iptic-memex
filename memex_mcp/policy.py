"""Effective MCP permission, shared by startup and provider pass-through."""

from utils.tool_args import get_bool


def mcp_enabled(session):
    if getattr(session, 'private', False):
        return False
    active = get_bool({'value': session.get_option('MCP', 'active', fallback=False)}, 'value', False)
    policy = getattr(session, 'mcp_autoload_policy', None)
    return bool(active and (policy is None or policy.get('enabled', False)))
