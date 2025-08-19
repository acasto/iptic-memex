from __future__ import annotations

from typing import Any, Dict, List


def _auto_description(cmd_key: str, handler_name: str) -> str:
    key = (cmd_key or '').strip()
    handler = (handler_name or '').strip()
    return f"Assistant command {key} mapped to action '{handler}'."


def _required_for_command(cmd_key: str) -> List[str]:
    """Heuristic required fields per command for Phase 1.

    Keep permissive; add only obvious requirements to reduce false negatives.
    """
    key = (cmd_key or '').upper()
    if key == 'CMD':
        return ['command']
    if key == 'FILE':
        # 'mode' is needed to route; 'file' is commonly required for writes/reads
        return ['mode', 'file']
    if key == 'WEBSEARCH':
        return ['query']
    if key == 'OPENLINK':
        return ['url']
    if key == 'YOUTRACK':
        return ['mode']
    if key == 'MATH':
        return ['expression']
    if key == 'MEMORY':
        return ['action']
    return []


def build_official_tool_specs(session) -> List[Dict[str, Any]]:
    """Build OpenAI Chat Completions tool specs from assistant command registry.

    Returns a list of objects in the shape expected by `tools` parameter of
    Chat Completions: [{"type":"function", "function":{...}}]
    """
    # Load the registry from the action to be the single source of truth
    commands_action = session.get_action('assistant_commands')
    if not commands_action or not getattr(commands_action, 'commands', None):
        return []

    specs: List[Dict[str, Any]] = []
    for cmd_key, info in commands_action.commands.items():
        try:
            handler = info.get('function', {})
            handler_name = handler.get('name', '')
            # Known args per command
            arg_names = list(info.get('args', []) or [])
            # Always allow an additional freeform content body
            properties = {name: {"type": "string"} for name in arg_names}
            properties['content'] = {"type": "string"}

            required = _required_for_command(cmd_key)

            function_obj = {
                "name": str(cmd_key).lower(),
                "description": _auto_description(cmd_key, handler_name),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    # Keep permissive for Phase 1; include obvious required only
                    "required": required,
                    "additionalProperties": True,
                },
            }

            specs.append({"type": "function", "function": function_obj})
        except Exception:
            continue

    return specs


def build_anthropic_tool_specs(session) -> List[Dict[str, Any]]:
    """Build Anthropic Messages API tool specs from assistant command registry.

    Returns a list of dicts with shape:
      [{"name": str, "description": str, "input_schema": {...}}]
    """
    commands_action = session.get_action('assistant_commands')
    if not commands_action or not getattr(commands_action, 'commands', None):
        return []

    specs: List[Dict[str, Any]] = []
    for cmd_key, info in commands_action.commands.items():
        try:
            handler = info.get('function', {})
            handler_name = handler.get('name', '')
            arg_names = list(info.get('args', []) or [])
            properties = {name: {"type": "string"} for name in arg_names}
            properties['content'] = {"type": "string"}

            required = _required_for_command(cmd_key)

            specs.append({
                'name': str(cmd_key).lower(),
                'description': _auto_description(cmd_key, handler_name),
                'input_schema': {
                    'type': 'object',
                    'properties': properties,
                    'required': required,
                }
            })
        except Exception:
            continue

    return specs
