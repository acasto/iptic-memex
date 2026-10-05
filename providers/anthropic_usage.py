"""Anthropic accounting: cache reads/writes are separate from ordinary input."""

from copy import deepcopy
from dataclasses import dataclass

from providers.api_utils import field


@dataclass
class Usage:
    """Counts from one request or an aggregate of requests."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_writes: int = 0
    cache_hits: int = 0
    cache_writes_1h: int = 0
    time_elapsed: float = 0.0

    def update(self, other):
        """Add another request's independent counts."""
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + (getattr(other, name) or 0))

    def merge(self, usage):
        """Apply cumulative streaming counts, keeping absent fields unchanged."""
        for native, name in (
            ('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
            ('cache_creation_input_tokens', 'cache_writes'),
            ('cache_read_input_tokens', 'cache_hits'),
        ):
            value = field(usage, native)
            if value is not None:
                setattr(self, name, value)
        value = field(field(usage, 'cache_creation'), 'ephemeral_1h_input_tokens')
        if value is not None:
            self.cache_writes_1h = value

    @property
    def total_input(self):
        """All input tokens processed, including cached input."""
        return self.input_tokens + self.cache_writes + self.cache_hits


class AnthropicUsage:
    """Accumulate costs at request time and preserve them through rebuilds."""

    def _init_usage(self):
        self.current_usage = Usage()
        self.total_usage = Usage()
        self._usage_known = False
        self._cost_totals = {name: 0.0 for name in (
            'input_cost', 'output_cost', 'cache_write_cost', 'cache_read_cost', 'cache_savings',
        )}

    def _record_usage(self, usage):
        self.current_usage.update(usage)
        self.total_usage.update(usage)
        try:
            p = self._usage_params
            unit = float(p.get('price_unit', 1000000))
            price_in = float(p.get('price_in', 0))
            price_out = float(p.get('price_out', 0))
            write = float(p.get('price_cache_write', p.get('price_cache_in', price_in)))
            read = float(p.get('price_cache_read', p.get('price_cache_out', price_in)))
            write_1h = float(p.get('price_cache_write_1h', 2 * price_in))
            hour = min(usage.cache_writes, usage.cache_writes_1h)
            costs = {
                'input_cost': usage.input_tokens * price_in / unit,
                'output_cost': usage.output_tokens * price_out / unit,
                'cache_write_cost': ((usage.cache_writes - hour) * write + hour * write_1h) / unit,
                'cache_read_cost': usage.cache_hits * read / unit,
                'cache_savings': max(0, usage.cache_hits * (price_in - read) / unit),
            }
            if self._cost_totals is not None:
                for name, value in costs.items():
                    self._cost_totals[name] += value
        except (ValueError, TypeError, ZeroDivisionError):
            self._cost_totals = None

    def get_usage(self):
        """Return inclusive input totals and explicit cache/uncached breakdowns."""
        result = {}
        for prefix, usage in (('total', self.total_usage), ('turn', self.current_usage)):
            result.update({
                f'{prefix}_in': usage.total_input,
                f'{prefix}_out': usage.output_tokens,
                f'{prefix}_cache_writes': usage.cache_writes,
                f'{prefix}_cache_hits': usage.cache_hits,
                f'{prefix}_cache_writes_1h': usage.cache_writes_1h,
                f'{prefix}_uncached_in': usage.input_tokens,
                f'{prefix}_content_tokens': usage.total_input,
            })
        result['total_tokens'] = self.total_usage.total_input + self.total_usage.output_tokens
        result['turn_total'] = self.current_usage.total_input + self.current_usage.output_tokens
        result['total_time'] = self.total_usage.time_elapsed
        result['turn_time'] = self.current_usage.time_elapsed
        result['turn_usage_known'] = self._usage_known
        if self._cost_totals is not None:
            costs = self._cost_totals
            result['total_input_cost'] = (costs['input_cost'] + costs['cache_write_cost']
                                          + costs['cache_read_cost'])
            result['total_uncached_input_cost'] = costs['input_cost']
            for name in ('output_cost', 'cache_write_cost', 'cache_read_cost', 'cache_savings'):
                result[f'total_{name}'] = costs[name]
        return result

    def reset_usage(self):
        """Clear accounting and any unconsumed response state."""
        self._init_usage()
        self._clear_response()

    def set_usage(self, stats):
        """Restore totals without recalculating them at the new model's prices."""
        self._init_usage()
        writes = stats.get('total_cache_writes', 0)
        reads = stats.get('total_cache_hits', stats.get('total_cached', 0))
        # Older Anthropic statistics counted only ordinary input in total_in.
        legacy = 'total_cache_writes' in stats and 'total_uncached_in' not in stats
        ordinary = (stats.get('total_in', 0) if legacy
                    else max(0, stats.get('total_in', 0) - writes - reads))
        self.total_usage = Usage(
            input_tokens=stats.get('total_uncached_in', ordinary),
            output_tokens=stats.get('total_out', 0), cache_writes=writes, cache_hits=reads,
            cache_writes_1h=stats.get('total_cache_writes_1h', 0),
            time_elapsed=stats.get('total_time', 0),
        )
        self._cost_totals.update({
            'input_cost': stats.get('total_uncached_input_cost', stats.get('total_input_cost', 0)),
            'output_cost': stats.get('total_output_cost', 0),
            'cache_write_cost': stats.get('total_cache_write_cost', 0),
            'cache_read_cost': stats.get('total_cache_read_cost', 0),
            'cache_savings': stats.get('total_cache_savings', 0),
        })
        if ((self.total_usage.total_input or self.total_usage.output_tokens)
                and ('total_input_cost' not in stats or 'total_output_cost' not in stats)):
            self._cost_totals = None

    def get_cost(self):
        """Return accumulated costs, independent of current pricing settings."""
        if self._cost_totals is None:
            return None
        result = deepcopy(self._cost_totals)
        result['total_cost'] = sum(value for key, value in result.items() if key != 'cache_savings')
        return {name: round(value, 6) for name, value in result.items()}
