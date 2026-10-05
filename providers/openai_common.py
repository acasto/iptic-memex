"""Shared normalization and accounting for the two OpenAI wire protocols."""

import ast
import json
import inspect
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
        return value.model_dump(mode='json', exclude_none=True)
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


def strict_schema(schema: dict, nullable_optionals: bool = False) -> dict:
    """Normalize every object in a copy of a function's strict JSON schema."""
    result = deepcopy(schema)

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        properties = node.get('properties')
        typ = node.get('type')
        if typ == 'object' or (isinstance(typ, list) and 'object' in typ) or isinstance(properties, dict):
            properties = node.setdefault('properties', {})
            required = node.get('required') or []
            if nullable_optionals:
                for name, child in list(properties.items()):
                    if name in required or not isinstance(child, dict):
                        continue
                    typ = child.get('type')
                    if typ == 'null' or (isinstance(typ, list) and 'null' in typ):
                        continue
                    alternatives = child.get('anyOf')
                    if isinstance(alternatives, list):
                        if not any(field(it, 'type') == 'null' for it in alternatives):
                            alternatives.append({'type': 'null'})
                    else:
                        properties[name] = {'anyOf': [child, {'type': 'null'}]}
                        if 'description' in child:
                            properties[name]['description'] = child['description']
            node['additionalProperties'] = False
            node['required'] = list(properties)
        # Only traverse schema-bearing keywords, never enum/default data.
        for key in ('properties', '$defs', 'definitions', 'patternProperties'):
            for child in (node.get(key) or {}).values():
                visit(child)
        for key in ('items', 'prefixItems', 'anyOf', 'oneOf', 'allOf', 'not'):
            visit(node.get(key))

    visit(result)
    return result


class OpenAIUsage:
    """Track token subsets and charge each request at its own model's prices."""

    def _init_usage(self):
        self.turn_usage = None
        self.running_usage = {'total_in': 0, 'total_out': 0, 'total_time': 0.0}
        self._cost_totals = {'input_cost': 0.0, 'output_cost': 0.0}

    @staticmethod
    def _usage_values(usage):
        input_details = field(usage, 'input_tokens_details')
        if input_details is None:
            input_details = field(usage, 'prompt_tokens_details')
        output_details = field(usage, 'output_tokens_details')
        if output_details is None:
            output_details = field(usage, 'completion_tokens_details')
        return {
            'total_in': field(usage, 'input_tokens', field(usage, 'prompt_tokens', 0)) or 0,
            'total_out': field(usage, 'output_tokens', field(usage, 'completion_tokens', 0)) or 0,
            'cached_tokens': field(input_details, 'cached_tokens', 0) or 0,
            'reasoning_tokens': field(output_details, 'reasoning_tokens', 0) or 0,
            'accepted_prediction_tokens': field(output_details, 'accepted_prediction_tokens', 0) or 0,
            'rejected_prediction_tokens': field(output_details, 'rejected_prediction_tokens', 0) or 0,
        }

    @staticmethod
    def _price(values, params):
        unit = float(params.get('price_unit', 1000000))
        price_in = float(params.get('price_in', 0))
        price_cached = float(params.get('price_cache_in', price_in))
        price_out = float(params.get('price_out', 0))
        cached = min(values['total_in'], values.get('cached_tokens', 0))
        # Both APIs already include reasoning in output/completion_tokens.
        output = values['total_out']
        return {
            'input_cost': ((values['total_in'] - cached) * price_in + cached * price_cached) / unit,
            'output_cost': output * price_out / unit,
        }

    def _record_usage(self, usage):
        if usage is None:
            return
        self.turn_usage = usage
        values = self._usage_values(usage)
        if not hasattr(self, '_cost_totals'):
            self._cost_totals = {'input_cost': 0.0, 'output_cost': 0.0}
        for key, value in values.items():
            self.running_usage[key] = self.running_usage.get(key, 0) + value
        try:
            params = getattr(self, '_usage_params', None)
            costs = self._price(values, params if params is not None else self.session.get_params())
            if self._cost_totals is not None:
                for key, value in costs.items():
                    self._cost_totals[key] += value
        except (ValueError, TypeError, ZeroDivisionError):
            self._cost_totals = None

    def get_usage(self):
        """Return totals and the latest request's metrics in Memex's usual shape."""
        stats = {key: self.running_usage.get(key, 0) for key in
                 ('total_in', 'total_out', 'total_time')}
        stats['total_tokens'] = stats['total_in'] + stats['total_out']
        metrics = {
            'cached': 'cached_tokens', 'reasoning': 'reasoning_tokens',
            'accepted_predictions': 'accepted_prediction_tokens',
            'rejected_predictions': 'rejected_prediction_tokens',
        }
        for name, key in metrics.items():
            if key in self.running_usage:
                stats[f'total_{name}'] = self.running_usage[key]
        if self.turn_usage is not None:
            values = self._usage_values(self.turn_usage)
            stats.update(turn_in=values['total_in'], turn_out=values['total_out'],
                         turn_total=values['total_in'] + values['total_out'])
            for name, key in metrics.items():
                stats[f'turn_{name}'] = values[key]
        if getattr(self, '_cost_totals', None) is not None:
            stats['total_input_cost'] = self._cost_totals['input_cost']
            stats['total_output_cost'] = self._cost_totals['output_cost']
        return stats

    def reset_usage(self):
        """Clear token and cost totals."""
        self._init_usage()

    def set_usage(self, stats):
        """Restore aggregate accounting when Session rebuilds a provider."""
        self._init_usage()
        for key in ('total_in', 'total_out', 'total_time'):
            self.running_usage[key] = stats.get(key, 0)
        for name, key in (
            ('cached', 'cached_tokens'), ('reasoning', 'reasoning_tokens'),
            ('accepted_predictions', 'accepted_prediction_tokens'),
            ('rejected_predictions', 'rejected_prediction_tokens'),
        ):
            self.running_usage[key] = stats.get(f'total_{name}', 0)
        self._cost_totals = {
            'input_cost': stats.get('total_input_cost', 0),
            'output_cost': stats.get('total_output_cost', 0),
        }

    def get_cost(self):
        """Return accumulated costs without repricing older model calls."""
        costs = getattr(self, '_cost_totals', None)
        if costs is None:
            return None
        return {
            'input_cost': round(costs['input_cost'], 6),
            'output_cost': round(costs['output_cost'], 6),
            'total_cost': round(costs['input_cost'] + costs['output_cost'], 6),
        }
