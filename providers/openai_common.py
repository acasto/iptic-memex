"""Shared normalization and accounting for the two OpenAI wire protocols."""

import inspect
from copy import deepcopy
from providers.api_utils import (
    as_dict, excluded_parameters, extra_body, field, parse_tool_arguments, sdk_params,
)


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
        self._cache_write_headers = {}
        self.running_usage = {'total_in': 0, 'total_out': 0, 'total_time': 0.0}
        self._cost_totals = {'input_cost': 0.0, 'output_cost': 0.0}

    @staticmethod
    def _usage_values(usage, headers=None):
        input_details = field(usage, 'input_tokens_details')
        if input_details is None:
            input_details = field(usage, 'prompt_tokens_details')
        output_details = field(usage, 'output_tokens_details')
        if output_details is None:
            output_details = field(usage, 'completion_tokens_details')
        values = {
            'total_in': field(usage, 'input_tokens', field(usage, 'prompt_tokens', 0)) or 0,
            'total_out': field(usage, 'output_tokens', field(usage, 'completion_tokens', 0)) or 0,
            'cached_tokens': field(input_details, 'cached_tokens',
                                   field(usage, 'cached_tokens', 0)) or 0,
            'cache_write_tokens': field(input_details, 'cache_write_tokens', 0) or 0,
            'reasoning_tokens': field(output_details, 'reasoning_tokens', 0) or 0,
            'accepted_prediction_tokens': field(output_details, 'accepted_prediction_tokens', 0) or 0,
            'rejected_prediction_tokens': field(output_details, 'rejected_prediction_tokens', 0) or 0,
        }
        # Kimi reports the actual write tier in headers. Request TTL is not
        # reliable: an existing cache prefix retains the TTL of its first write.
        for ttl in ('5m', '1h'):
            value = (headers or {}).get(f'msh-usage-cache-write-tokens-{ttl}')
            try:
                count = max(0, int(value))
            except (ValueError, TypeError):
                count = 0
            values[f'cache_write_tokens_{ttl}'] = count
        split = values['cache_write_tokens_5m'] + values['cache_write_tokens_1h']
        if field(input_details, 'cache_write_tokens') is None:
            values['cache_write_tokens'] = split
        # Unclassified writes use the generic rate rather than guessing a TTL.
        remaining = max(0, values['cache_write_tokens'])
        for ttl in ('5m', '1h'):
            key = f'cache_write_tokens_{ttl}'
            values[key] = min(remaining, values[key])
            remaining -= values[key]
        return values

    def _create_response(self, resource, params):
        """Retain accounting headers while returning the usual SDK response."""
        self._cache_write_headers = {}
        create = resource.create
        kwargs = sdk_params(create, params)
        raw_create = getattr(getattr(resource, 'with_raw_response', None), 'create', None)
        # Lightweight clients and compatibility shims may not support the SDK's
        # raw-response header. Their normal create path remains supported.
        try:
            supports_headers = 'extra_headers' in inspect.signature(create).parameters
        except (ValueError, TypeError):
            supports_headers = False
        if not callable(raw_create) or not supports_headers:
            response = create(**kwargs)
            headers = field(field(response, 'response'), 'headers', {}) or {}
            self._cache_write_headers = {str(k).lower(): v for k, v in headers.items()}
            return response
        raw = raw_create(**kwargs)
        self._cache_write_headers = {str(k).lower(): v for k, v in raw.headers.items()}
        try:
            return raw.parse()
        except BaseException:
            if params.get('stream'):
                raw.http_response.close()
            raise
        finally:
            if not params.get('stream'):
                raw.http_response.close()

    @staticmethod
    def _price(values, params):
        unit = float(params.get('price_unit', 1000000))
        price_in = float(params.get('price_in', 0))
        price_cached = float(params.get('price_cache_read', params.get('price_cache_in', price_in)))
        price_write = float(params.get('price_cache_write', price_in))
        price_write_5m = float(params.get('price_cache_write_5m', price_write))
        price_write_1h = float(params.get('price_cache_write_1h', price_write))
        price_out = float(params.get('price_out', 0))
        cached = min(values['total_in'], max(0, values.get('cached_tokens', 0)))
        writes = min(values['total_in'] - cached, max(0, values.get('cache_write_tokens', 0)))
        writes_5m = min(writes, values.get('cache_write_tokens_5m', 0))
        writes_1h = min(writes - writes_5m, values.get('cache_write_tokens_1h', 0))
        write_cost = ((writes - writes_5m - writes_1h) * price_write
                      + writes_5m * price_write_5m + writes_1h * price_write_1h)
        # Both APIs already include reasoning in output/completion_tokens.
        output = values['total_out']
        return {
            'input_cost': ((values['total_in'] - cached - writes) * price_in
                           + cached * price_cached + write_cost) / unit,
            'output_cost': output * price_out / unit,
        }

    def _record_usage(self, usage):
        if usage is None:
            return
        self.turn_usage = usage
        values = self._usage_values(usage, self._cache_write_headers)
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
            'cache_writes': 'cache_write_tokens',
            'cache_writes_5m': 'cache_write_tokens_5m',
            'cache_writes_1h': 'cache_write_tokens_1h',
            'accepted_predictions': 'accepted_prediction_tokens',
            'rejected_predictions': 'rejected_prediction_tokens',
        }
        for name, key in metrics.items():
            if key in self.running_usage:
                stats[f'total_{name}'] = self.running_usage[key]
        if self.turn_usage is not None:
            values = self._usage_values(self.turn_usage, self._cache_write_headers)
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
            ('cache_writes', 'cache_write_tokens'),
            ('cache_writes_5m', 'cache_write_tokens_5m'),
            ('cache_writes_1h', 'cache_write_tokens_1h'),
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
