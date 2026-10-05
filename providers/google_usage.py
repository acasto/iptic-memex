"""Gemini usage: prompt counts include cache reads; candidates exclude thoughts."""

from copy import deepcopy

from providers.api_utils import field


class GoogleUsage:
    """Keep native counts and costs at the rates used for each request."""

    _count_fields = {
        'prompt_token_count': 'prompt_tokens',
        'candidates_token_count': 'candidate_tokens',
        'thoughts_token_count': 'reasoning_tokens',
        'cached_content_token_count': 'cached_tokens',
        'tool_use_prompt_token_count': 'tool_prompt_tokens',
        'total_token_count': 'total_tokens',
    }

    def _init_usage(self):
        self.turn_usage = None
        self._turn_time = 0
        self.total_usage = {name: 0 for name in self._count_fields.values()}
        self.total_usage['completion_tokens'] = 0
        self.total_usage['time_elapsed'] = 0.0
        self._cost_totals = dict(input_cost=0.0, output_cost=0.0, cache_read_cost=0.0,
                                 cache_savings=0.0)

    def _record_usage(self, native, elapsed: float):
        counts = {name: field(native, key, 0) or 0 for key, name in self._count_fields.items()}
        counts['completion_tokens'] = counts['candidate_tokens'] + counts['reasoning_tokens']
        if field(native, 'total_token_count') is None:
            counts['total_tokens'] = counts['prompt_tokens'] + counts['completion_tokens']
        counts['time_elapsed'] = elapsed
        known = native is not None and any(field(native, key) is not None for key in self._count_fields)
        self.turn_usage = counts if known else None
        self._turn_time = elapsed
        for name, value in counts.items():
            self.total_usage[name] += value
        if self._cost_totals is None:
            return
        try:
            p = self._usage_params
            unit = float(p.get('price_unit', 1000000))
            if unit <= 0:
                raise ValueError('price_unit must be positive')
            price_in = float(p.get('price_in', 0))
            price_out = float(p.get('price_out', 0))
            read = float(p.get('price_cache_read', p.get('price_cache_in', price_in)))
            cache = min(counts['cached_tokens'], counts['prompt_tokens'])
            cached_cost = cache * read / unit
            self._cost_totals['input_cost'] += (
                (counts['prompt_tokens'] - cache) * price_in / unit + cached_cost)
            self._cost_totals['output_cost'] += counts['completion_tokens'] * price_out / unit
            self._cost_totals['cache_read_cost'] += cached_cost
            self._cost_totals['cache_savings'] += max(0, cache * (price_in - read) / unit)
        except (ValueError, TypeError, ZeroDivisionError):
            self._cost_totals = None

    def get_usage(self) -> dict:
        """Expose inclusive output counts with separate thoughts and candidate counts."""
        result = {}
        for prefix, counts in (('total', self.total_usage), ('turn', self.turn_usage or {})):
            result.update({
                f'{prefix}_in': counts.get('prompt_tokens', 0),
                f'{prefix}_out': counts.get('completion_tokens', 0),
                f'{prefix}_cached': counts.get('cached_tokens', 0),
                f'{prefix}_reasoning': counts.get('reasoning_tokens', 0),
                f'{prefix}_candidates': counts.get('candidate_tokens', 0),
                f'{prefix}_tool_prompt': counts.get('tool_prompt_tokens', 0),
            })
        result.update({
            'total_tokens': self.total_usage['total_tokens'],
            'turn_total': (self.turn_usage or {}).get('total_tokens', 0),
            'total_time': self.total_usage['time_elapsed'],
            'turn_time': getattr(self, '_turn_time', 0),
            'turn_usage_known': self.turn_usage is not None,
            # Preserve the provider's existing public cache key.
            'cached_tokens': self.total_usage['cached_tokens'],
        })
        if self._cost_totals is not None:
            for name, value in self._cost_totals.items():
                result[f'total_{name}'] = value
        return result

    def set_usage(self, stats: dict) -> None:
        """Restore counters and recorded costs without repricing old requests."""
        self._init_usage()
        output = stats.get('total_out', 0)
        reasoning = stats.get('total_reasoning', 0)
        self.total_usage.update({
            'prompt_tokens': stats.get('total_in', 0), 'completion_tokens': output,
            'candidate_tokens': stats.get('total_candidates', max(0, output - reasoning)),
            'reasoning_tokens': reasoning,
            'cached_tokens': stats.get('total_cached', stats.get('cached_tokens', 0)),
            'tool_prompt_tokens': stats.get('total_tool_prompt', 0),
            'total_tokens': stats.get('total_tokens', stats.get('total_in', 0) + output),
            'time_elapsed': stats.get('total_time', 0),
        })
        self._cost_totals.update({name: stats.get(f'total_{name}', 0)
                                  for name in self._cost_totals})
        if ((self.total_usage['prompt_tokens'] or output)
                and ('total_input_cost' not in stats or 'total_output_cost' not in stats)):
            self._cost_totals = None

    def reset_usage(self) -> None:
        """Clear statistics and unconsumed response state when clearing the chat."""
        self._init_usage()
        self._clear_response()

    def get_cost(self) -> dict | None:
        """Return costs observed so far, including thinking and cached input."""
        if self._cost_totals is None:
            return None
        result = deepcopy(self._cost_totals)
        result['total_cost'] = result['input_cost'] + result['output_cost']
        return {key: round(value, 6) for key, value in result.items()}
