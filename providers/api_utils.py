"""Protocol-independent SDK and configuration normalization helpers."""

import ast
import inspect
import json
from copy import deepcopy
from typing import Any


def field(value: Any, name: str, default: Any = None) -> Any:
    """Read an SDK object or a JSON fixture without conflating null with zero."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def as_dict(value: Any) -> Any:
    """Copy an SDK output item into serializable transcript metadata."""
    if isinstance(value, dict):
        return {key: as_dict(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_dict(child) for child in value]
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', exclude_none=True, warnings=False)
    if hasattr(value, '__dict__'):
        return as_dict(vars(value))
    return deepcopy(value)


def extra_body(value: dict | str | None) -> dict:
    """Accept config dictionaries or literal dictionaries, never executable code."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = ast.literal_eval(value)
    if not isinstance(value, dict):
        raise ValueError('extra_body must be a dictionary')
    return deepcopy(value)


def excluded_parameters(params: dict) -> set[str]:
    """Normalize the config's parameter exclusion list for either API."""
    value = params.get('excluded_parameters') or []
    if isinstance(value, str):
        value = value.split(',')
    return {str(name).strip() for name in value}


def sdk_params(create, params: dict) -> dict:
    """Forward new wire fields through extra_body when an older SDK needs it."""
    try:
        signature = inspect.signature(create)
    except (ValueError, TypeError):
        return params
    supported = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in supported.values()):
        return params
    if 'extra_body' not in supported:
        return params
    result = dict(params)
    body = deepcopy(result.get('extra_body') or {})
    for name in list(result):
        if name not in supported:
            # The SDK's streaming switch must remain an SDK argument.
            if name == 'stream':
                continue
            body.setdefault(name, result.pop(name))
    if body:
        result['extra_body'] = body
    return result


def parse_tool_arguments(arguments: Any) -> tuple[dict, bool]:
    """Return an argument object and whether the call must be rejected."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return {}, True
    if not isinstance(arguments, dict):
        return {}, True
    return arguments, False

