from base_classes import InteractionAction
from typing import Any


class PersistStatsAction(InteractionAction):
    """
    Action for persisting usage statistics to database
    """
    def __init__(self, session):
        self.session = session
        self.provider = session.get_provider()
        self.params = session.get_params()

        # Define stats schema with typing and behavior
        self.stats_schema = {
            'tokens_in': {
                'type': int,
                'accumulate': True,
                'source': 'total_in',
                'prefixes': ['total', 'model_{model}']
            },
            'tokens_out': {
                'type': int,
                'accumulate': True,
                'source': 'total_out',
                'prefixes': ['total', 'model_{model}']
            },
            'time': {
                'type': float,
                'accumulate': True,
                'source': 'total_time',
                'prefixes': ['total', 'model_{model}']
            },
            'last_model': {
                'type': str,
                'accumulate': False,
                'source': 'model',
                'prefixes': ['session']
            }
        }

    def _get_value(self, stat_name: str, stats: dict[str, Any]) -> Any:
        """Get value from stats based on schema source"""
        source = self.stats_schema[stat_name]['source']
        if source in stats:
            return stats[source]
        elif source in self.params:
            return self.params[source]
        return None

    def run(self, args=None):
        """
        Persist current usage statistics to the database
        """
        if not self.provider:
            return

        current_stats = self.provider.get_usage()
        if not current_stats:
            return

        # Process each stat according to schema
        for stat_name, schema in self.stats_schema.items():
            current_value = self._get_value(stat_name, current_stats)
            if current_value is None:
                continue

            # Handle each prefix for the stat
            for prefix in schema['prefixes']:
                # Format prefix if it contains placeholders
                formatted_prefix = prefix.format(model=self.params.get('model', 'unknown'))
                db_key = f"{formatted_prefix}_{stat_name}"

                if schema['accumulate']:
                    # Get and convert stored value
                    stored_value = self.session.utils.storage.get(db_key)
                    stored_value = schema['type'](stored_value) if stored_value else schema['type'](0)
                    # Store updated total
                    self.session.utils.storage.set(db_key, str(stored_value + current_value))
                else:
                    # Simply store current value
                    self.session.utils.storage.set(db_key, str(current_value))
